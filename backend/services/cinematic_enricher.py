"""
Cinematic Enricher — LLM-powered rich descriptions for embedding generation.
Uses Groq (AsyncOpenAI-compatible) to produce tone/theme/style descriptions
that replace the shallow title+genre concatenation for vector similarity.
"""
import asyncio
import logging
import os
import re
from typing import List

logger = logging.getLogger(__name__)


def _get_model_chain() -> list[str]:
    """Return the LLM model chain based on available API keys."""
    if os.getenv("GROQ_API_KEY"):
        return [
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
        ]
    if os.getenv("GEMINI_API_KEY"):
        return ["gemini-2.5-flash"]
    return []


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

    if force_model:
        models = [force_model]
    else:
        models = _get_model_chain()
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
                max_tokens=1000,
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
                                max_tokens=1000,
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


async def generate_profile_summary(
    top_rated_films: list[dict],  # [{"title": str, "year": int, "rating": float, "genres": list[str]}]
    dominant_genres: list[str],
    groq_client,
) -> str | None:
    """
    Based on these highly-rated films and genres, extract the cinematic keywords that define this person's taste profile.

    Top rated films: {film_list}
    Dominant genres: {genres}

    Respond with ONLY a comma-separated list of 12-15 keywords. No sentences, no explanations, no punctuation other than commas.
    Focus on: tone (e.g. melancholic, darkly comedic), themes (e.g. moral ambiguity, identity), visual style (e.g. handheld gritty, long takes), pacing (e.g. slow burn, frenetic), and cinematic movements or affinities (e.g. French New Wave, A24, Korean revenge).
    Example format: slow burn, melancholic, morally complex, atmospheric, character-driven, contemplative, humanist, European art house, naturalistic lighting, existential themes, quiet intensity, bittersweet
    Uses llama-4-scout-17b exclusively for high-fidelity profiling.
    """
    if not groq_client or not top_rated_films:
        return None

    films_str = "\n".join([
        f"- {f['title']} ({f['year']}) [{f['rating']} stars] - Genres: {', '.join(f.get('genres', []))}"
        for f in top_rated_films[:10]
    ])
    genres_str = ", ".join(dominant_genres) if dominant_genres else "Various"

    prompt = (
        "USER PROFILE DATA:\n"
        f"Top Rated Films:\n{films_str}\n\n"
        f"Dominant Genres: {genres_str}\n\n"
        "TASK:\n"
        "Extract 12-15 keywords that define this user's cinematic taste. "
        "Respond with ONLY a comma-separated list. No sentences, no explanation.\n"
        "Focus on: tone, themes, visual style, pacing, cinematic movements.\n"
        "Example: slow burn, melancholic, morally complex, atmospheric, character-driven, "
        "contemplative, humanist, European art house, naturalistic lighting, existential themes, "
        "quiet intensity, bittersweet"
    )

    messages = [
        {
            "role": "system",
            "content": "You are a film critic. Respond ONLY with a comma-separated list of keywords. No sentences, no markdown, no explanation.",
        },
        {"role": "user", "content": prompt},
    ]

    try:
        response = await groq_client.chat.completions.create(
            model=_get_model_chain()[0],
            messages=messages,
            temperature=0.5,
            max_tokens=1000,
        )
        summary = response.choices[0].message.content.strip()
        if summary and len(summary) > 30:
            return summary
    except Exception as e:
        logger.error(f"Profile summary generation failed: {e}")
    
    return None
