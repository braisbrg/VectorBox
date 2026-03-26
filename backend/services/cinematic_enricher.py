"""
Cinematic Enricher — LLM-powered rich descriptions for embedding generation.
Uses Groq (AsyncOpenAI-compatible) to produce tone/theme/style descriptions
that replace the shallow title+genre concatenation for vector similarity.
"""
import asyncio
import logging
import re
from typing import List

logger = logging.getLogger(__name__)


class DailyLimitExhausted(Exception):
    """Raised when the forced model's daily token/request limit is exhausted."""
    pass


def _build_legacy_text(
    title: str,
    overview: str,
    genres: list[str],
    keywords: list[str],
) -> str:
    """Legacy fallback: same concatenation used before LLM enrichment."""
    parts: list[str] = []
    if title:
        parts.append(title)
    if overview:
        parts.append(overview)
    if genres:
        parts.append(f"Genres: {', '.join(genres)}")
    if keywords:
        parts.append(f"Themes: {', '.join(keywords[:15])}")
    return ". ".join(parts) if parts else title or "Unknown"


def _parse_retry_after(error_message: str) -> float | None:
    """
    Extract wait time from Groq 429 error message.
    Returns seconds to wait, or None if not parsable.
    Example: 'Please try again in 1m26.4s' → 86.4
    """
    match = re.search(r"try again in (\d+(?:\.\d+)?)m(\d+(?:\.\d+)?)s", error_message)
    if match:
        return float(match.group(1)) * 60 + float(match.group(2))
    match = re.search(r"try again in (\d+(?:\.\d+)?)s", error_message)
    if match:
        return float(match.group(1))
    match = re.search(r"try again in (\d+(?:\.\d+)?)m", error_message)
    if match:
        return float(match.group(1)) * 60
    return None


def _is_daily_limit(error_message: str) -> bool:
    """Check if 429 error is a per-day limit (RPD/TPD) vs per-minute (RPM/TPM)."""
    return "per day" in error_message.lower() or "(rpd)" in error_message.lower() or "(tpd)" in error_message.lower()


async def generate_cinematic_description(
    title: str,
    overview: str,
    genres: list[str],
    keywords: list[str],
    directors: list[str],
    cast: list[str],
    year: int,
    groq_client,  # AsyncOpenAI pointing to Groq — receive as parameter, never instantiate here
    force_model: str = None,
) -> tuple[str, str | None]:
    """
    Use Groq to generate a rich cinematic description for embedding.
    Falls back to the legacy concatenation if Groq fails.

    Returns: (description, model_id) — model_id is None when fallback is used.

    Rate limit strategy:
    - Per-minute 429 (RPM/TPM): wait the indicated time, retry SAME model
    - Per-day 429 (RPD/TPD): fall back to NEXT model in chain
    """
    fallback = _build_legacy_text(title, overview, genres, keywords)

    if groq_client is None:
        return fallback, None

    genres_str = ", ".join(genres) if genres else "Unknown"
    keywords_str = ", ".join(keywords[:10]) if keywords else "None"
    directors_str = ", ".join(directors) if directors else "Unknown"
    cast_str = ", ".join(cast[:3]) if cast else "Unknown"

    prompt = (
        f"Movie: {title} ({year})\n"
        f"Genres: {genres_str}\n"
        f"Keywords: {keywords_str}\n"
        f"Directors: {directors_str}\n"
        f"Cast: {cast_str}\n"
        f"Plot: {overview or 'No plot available.'}\n\n"
        "Write a rich cinematic description of this film in English. "
        "Plain text only — no markdown, no headers, no bullet points. "
        "Maximum 80 words. Cover ALL of the following:\n"
        "- Tone (e.g. melancholic, tense, comedic, dreamlike)\n"
        "- Themes (e.g. identity, revenge, family dysfunction)\n"
        "- Visual style (e.g. handheld gritty, static long takes, neon-lit)\n"
        "- Pacing (e.g. slow burn, frenetic, episodic)\n"
        "- Audience affinity (e.g. fans of Kubrick, A24 films, Korean revenge cinema)\n"
        "- Mood keywords (3-5 single words at the end)"
    )

    # Model fallback chain: Scout → 70B → 8B → legacy
    if force_model:
        models = [force_model]
    else:
        models = [
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
        ]
    messages = [
        {
            "role": "system",
            "content": (
                "You are a cinematic analyst. Respond ONLY with the description. "
                "Always respond in English regardless of the film's language. "
                "No markdown, no headers."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    for model_id in models:
        try:
            response = await groq_client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=0.4,
                max_tokens=200,
            )
            description = response.choices[0].message.content.strip()
            if description and len(description) > 20:
                return description, model_id
            logger.warning(f"Groq ({model_id}) returned empty/short description for '{title}', trying next model")
        except Exception as e:
            error_str = str(e)

            # 429 rate limit — distinguish per-minute vs per-day
            if "429" in error_str:
                if _is_daily_limit(error_str):
                    if force_model:
                        raise DailyLimitExhausted(f"Daily limit reached for forced model: {force_model}")
                    # Daily limit exhausted → fall back to next model
                    logger.info(f"Groq ({model_id}) daily limit reached, falling back to next model")
                    continue
                else:
                    # Per-minute limit → wait and retry SAME model
                    wait_secs = _parse_retry_after(error_str)
                    if wait_secs and wait_secs < 120:
                        logger.info(f"Groq ({model_id}) minute rate limit hit, waiting {wait_secs:.0f}s...")
                        await asyncio.sleep(wait_secs + 1)  # +1s safety margin
                        # Retry same model
                        try:
                            response = await groq_client.chat.completions.create(
                                model=model_id,
                                messages=messages,
                                temperature=0.4,
                                max_tokens=200,
                            )
                            description = response.choices[0].message.content.strip()
                            if description and len(description) > 20:
                                return description, model_id
                        except Exception as retry_err:
                            logger.warning(f"Groq ({model_id}) retry after wait also failed: {retry_err}")
                    else:
                        logger.warning(f"Groq ({model_id}) rate limited, could not parse wait time, trying next model")
            else:
                logger.warning(f"Groq ({model_id}) failed for '{title}': {e}")

    logger.warning(f"All Groq models exhausted for '{title}', using legacy fallback")
    return fallback, None
