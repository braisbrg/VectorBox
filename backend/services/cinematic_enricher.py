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


# Per-model reasoning-effort overrides. Reasoning models otherwise spend the
# token budget on chain-of-thought: Qwen3 leaks it as inline <think>…</think>
# in the content unless effort='none'; gpt-oss empties out entirely at the
# default/high effort on long prompts (the 2026-06 reasoning experiment showed
# 'low' is the only reliable setting). Models not listed get no override.
_REASONING_EFFORT = {
    "qwen/qwen3-32b": "none",
    "qwen/qwen3.6-27b": "none",
    "openai/gpt-oss-120b": "low",
    "openai/gpt-oss-20b": "low",
}

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Remove any leaked <think>…</think> chain-of-thought (closed or dangling)
    so it never contaminates a cinematic_description fed to the embedder."""
    text = _THINK_RE.sub("", text)
    low = text.lower()
    if "<think>" in low:
        text = text[: low.rfind("<think>")]
    return text.strip()


def _get_model_chain() -> list[str]:
    """Return the LLM model chain based on available API keys.

    Order from the 2026-06 model sweep (V2-nameban prompt, all on Groq free
    tier @ 1K RPD each — re-verified live against /v1/models, limits 2026-06-29).
    Llama 4 Scout (decommission 2026-07-17), Llama 3.1 8B + Llama 3.3 70B
    (decommission 2026-08-16) all removed — Qwen3-32B is the head:
      1. Qwen3-32B — the most CONSISTENT full-structure output in the sweep
         (tone/themes/style/pacing/affinity/mood every time, ~72 words). 6K TPM.
         Reasoning model → effort='none' (see _REASONING_EFFORT).
      2. Qwen3.6-27B — top-tier prose, own 8K-TPM bucket (drained 2,872 films in
         the V2 re-enrich at ~0.5s/film); replaced Llama 3.3 70B 2026-06-29 when
         Groq deprecated it. Reasoning model → effort='none'.
      3. GPT-OSS-120B — richest prose (~81 words), reasoning (effort='low'). 8K TPM.
      4. GPT-OSS-20B — fast vendor-diverse floor (effort='low'). 8K TPM.
    """
    if os.getenv("GROQ_API_KEY"):
        return [
            "qwen/qwen3-32b",
            "qwen/qwen3.6-27b",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
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
    model_chain_override: list[str] | None = None,
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
    decade = f"{(year // 10) * 10}s" if year else "unspecified era"

    # Prompt recipe "v2" (2026-06). This text is the embedding recipe — every
    # caller (new ingest, RSS, re-enrich, maintenance) MUST use this exact
    # prompt so the catalogue stays in one vector space. Design rules, learned
    # the hard way from the title-token-leakage incident:
    #   - NO proper nouns of ANY kind in the output. Not the film's title /
    #     director / actor / character, and crucially not OTHER films, directors,
    #     studios, or franchises (the old prompt literally said "fans of Kubrick,
    #     A24 films" — those identity tokens create false similarity between
    #     unrelated films, exactly like the title leak did).
    #   - Comparables expressed ONLY as movements / subgenres / descriptive
    #     categories ("magic realism", "Korean revenge thriller").
    #   - No awards / "critically acclaimed" language: quality lives in VBS, not
    #     in the thematic embedding, and "award-winning" glues unrelated films.
    #   - Plain decade, not editorialised era tags ("Reagan-era", "post-9/11"),
    #     which inject shared political tokens across unrelated genres.
    system_content = (
        "You are a cinematic analyst writing concise, evocative descriptions for "
        "film-recommendation embeddings. Output plain prose only — no markdown, "
        "lists, or headers. Avoid clichés ('masterpiece', 'unforgettable', "
        "'must-see', 'tour de force'). Always respond in English regardless of "
        "the film's original language.\n\n"
        "STRICT NAME-BAN (critical for embedding quality): write NO proper noun "
        "that identifies a specific entity — not this film's title, not any "
        "director, actor, or character name, and NOT the names of OTHER films, "
        "directors, studios, or franchises (never write things like 'A24', "
        "'Kubrick', 'Studio Ghibli', 'Tarantino-esque', 'like The Matrix'). "
        "Describe authorial style and comparable cinema ONLY as movements, "
        "subgenres, or descriptive categories (e.g. 'magic realism', 'Korean "
        "revenge thriller', 'slow-burn folk horror'). Do not copy character or "
        "place names out of the plot synopsis. Identity tokens leak between "
        "unrelated films and corrupt similarity search."
    )
    prompt = (
        f"Film (title for your reference only — DO NOT write it): {title}\n"
        f"Decade: {decade}\n"
        f"Genres: {genres_str}\n"
        f"Themes/Keywords: {keywords_str}\n"
        f"Director(s) (context only — DO NOT name): {directors_str}\n"
        f"Lead cast (context only — DO NOT name): {cast_str}\n"
        f"Plot synopsis (for understanding — do NOT copy names from it): "
        f"{overview or 'No plot available.'}\n\n"
        "Style examples (these illustrate tone; note they contain NO names):\n\n"
        "Example A — a 1940s dark-fantasy war fable:\n"
        "A dreamlike anti-fascist fable that weaves brutal wartime violence with "
        "baroque dark fantasy through painterly amber-and-blue cinematography. "
        "Pacing alternates between tense military encounters and contemplative "
        "supernatural reverie. For viewers drawn to magic realism, gothic "
        "fairy-tale horror, and historical allegory. Mood: haunting, melancholic, "
        "mythic, brutal, transcendent.\n\n"
        "Example B — a 1990s mob epic:\n"
        "A propulsive epic tracking three decades of organized-crime life through "
        "an amoral first-person voiceover. Frenetic editing, needle-drop "
        "soundtrack, and restless camerawork give it a documentary-meets-rock-"
        "opera energy. Pacing escalates from nostalgic to paranoid as excess and "
        "betrayal unravel a brotherhood. For viewers drawn to non-romanticised "
        "ensemble crime cinema and kinetic realism. Mood: kinetic, decadent, "
        "paranoid, cynical, propulsive.\n\n"
        "Now write the description for the film above. Maximum 80 words. Cover "
        "tone, themes, visual/aural style, pacing, and the kind of viewer it "
        "appeals to (as categories, never named fans), in flowing prose. End "
        "with 3-5 single-word mood keywords. Absolutely no proper nouns."
    )

    if force_model:
        models = [force_model]
    elif model_chain_override is not None:
        models = model_chain_override
    else:
        models = _get_model_chain()
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": prompt},
    ]

    for model_id in models:
        effort = _REASONING_EFFORT.get(model_id)
        extra_body = {"reasoning_effort": effort} if effort else None
        try:
            response = await groq_client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=0.4,
                max_tokens=1000,
                extra_body=extra_body,
            )
            description = _strip_think(response.choices[0].message.content or "")
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
                                extra_body=extra_body,
                            )
                            description = _strip_think(response.choices[0].message.content or "")
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
    Uses the chain's primary model (currently `qwen/qwen3-32b`) for
    high-fidelity profiling.
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
