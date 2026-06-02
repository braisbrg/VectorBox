"""Prompt + model A/B for cinematic_description enrichment.

Compares 6 combinations on a curated 25-film panel:
  - scout-old / scout-new : isolates prompt effect (same model, both prompts)
  - 70b-old   / 70b-new   : isolates same, with the production candidate
  - oss120-new / oss20-new: OpenAI vendor diversification

Output: a markdown file with all combos side-by-side per film, so the user
can read and pick the winning combo. No DB writes, no Qdrant changes.

Rate-limit handling:
  - 0.5s pacing between requests (well under TPM/RPM for all models)
  - RPM 429: parse retry-after, wait once, retry SAME model
  - RPD 429 (daily exhausted): mark model exhausted, skip future calls for it
  - Other errors: log and continue to next combo
"""
import argparse
import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import AsyncOpenAI
from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


# ─────────────────────────────────────────────────────────────────────────────
# Curated experiment panel — 25 films across eras / genres / origins.
# Script skips any TMDB ID not present in the local DB.
# ─────────────────────────────────────────────────────────────────────────────
EXPERIMENT_FILM_IDS = [
    # Art-house / festival
    129,      # Spirited Away (2001) — Ghibli baseline
    1417,     # Pan's Labyrinth (2006) — del Toro dark fantasy
    496243,   # Parasite (2019) — Bong Joon-ho social thriller
    244786,   # Whiplash (2014) — Chazelle music drama
    872585,   # Oppenheimer (2023) — Nolan biopic
    # Mainstream blockbusters
    27205,    # Inception (2010)
    299534,   # Avengers: Endgame (2019)
    19995,    # Avatar (2009)
    11,       # Star Wars (1977)
    105,      # Back to the Future (1985)
    # Classic Hollywood
    238,      # The Godfather (1972)
    769,      # Goodfellas (1990)
    680,      # Pulp Fiction (1994)
    122,      # LOTR: Return of the King (2003)
    13,       # Forrest Gump (1994)
    # Spanish-language
    1422,     # Volver (2006) — Almodóvar
    11770,    # Mar adentro (2004) — Amenábar
    # Asian cinema
    372058,   # Your Name (2016) — Shinkai anime
    129392,   # Train to Busan (2016) — Korean horror
    # Horror / cult
    694,      # The Shining (1980)
    985,      # Eraserhead (1977) — Lynch
    274,      # The Silence of the Lambs (1991)
    # Sci-fi
    78,       # Blade Runner (1982)
    62,       # 2001: A Space Odyssey (1968)
    # Recent / streaming-era
    419430,   # Get Out (2017)
]
# Dedupe just in case
EXPERIMENT_FILM_IDS = list(dict.fromkeys(EXPERIMENT_FILM_IDS))


# ─────────────────────────────────────────────────────────────────────────────
# Era + country derivation helpers (used by the new prompt only)
# ─────────────────────────────────────────────────────────────────────────────
ERA_BUCKETS = [
    (0, 1929, "silent era / pre-Code"),
    (1930, 1945, "Golden Age Hollywood / pre-WWII"),
    (1946, 1959, "post-WWII / Cold War onset"),
    (1960, 1969, "1960s counterculture"),
    (1970, 1979, "1970s New Hollywood"),
    (1980, 1989, "1980s Reagan-era"),
    (1990, 1999, "1990s post-Cold War"),
    (2000, 2009, "2000s post-9/11"),
    (2010, 2019, "2010s streaming-era"),
    (2020, 2025, "post-pandemic"),
]

LANGUAGE_TO_LABEL = {
    "en": "English-language", "es": "Spanish-language", "fr": "French",
    "it": "Italian", "de": "German", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "cn": "Chinese", "pt": "Portuguese", "ru": "Russian",
    "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish",
}


def derive_era(year: Optional[int]) -> str:
    if not year:
        return "contemporary"
    for lo, hi, label in ERA_BUCKETS:
        if lo <= year <= hi:
            return label
    return "contemporary"


def derive_country(movie: Movie) -> str:
    if movie.omdb_countries:
        return ", ".join(movie.omdb_countries[:2])
    lang = (movie.original_language or "en").lower()
    return LANGUAGE_TO_LABEL.get(lang, lang)


# ─────────────────────────────────────────────────────────────────────────────
# Two prompts: current (production) and new (with era/country/awards/few-shot)
# ─────────────────────────────────────────────────────────────────────────────
def build_current_prompt(movie: Movie) -> list[dict]:
    """Verbatim copy of services/cinematic_enricher.py current prompt."""
    genres_str = ", ".join(movie.genres) if movie.genres else "Unknown"
    keywords_str = ", ".join((movie.keywords or [])[:10]) or "None"
    directors_str = ", ".join(movie.directors) if movie.directors else "Unknown"
    cast_str = ", ".join((movie.cast or [])[:3]) or "Unknown"
    return [
        {
            "role": "system",
            "content": (
                "You are a cinematic analyst. Respond ONLY with the description. "
                "Always respond in English regardless of the film's language. "
                "No markdown, no headers."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Movie: {movie.title} ({movie.year})\n"
                f"Genres: {genres_str}\n"
                f"Keywords: {keywords_str}\n"
                f"Directors: {directors_str}\n"
                f"Cast: {cast_str}\n"
                f"Plot: {movie.overview or 'No plot available.'}\n\n"
                "Write a rich cinematic description of this film in English. "
                "Plain text only — no markdown, no headers, no bullet points. "
                "Maximum 80 words. Cover ALL of the following:\n"
                "- Tone (e.g. melancholic, tense, comedic, dreamlike)\n"
                "- Themes (e.g. identity, revenge, family dysfunction)\n"
                "- Visual style (e.g. handheld gritty, static long takes, neon-lit)\n"
                "- Pacing (e.g. slow burn, frenetic, episodic)\n"
                "- Audience affinity (e.g. fans of Kubrick, A24 films, Korean revenge cinema)\n"
                "- Mood keywords (3-5 single words at the end)"
            ),
        },
    ]


def build_new_prompt(movie: Movie) -> list[dict]:
    """New prompt: adds country/era context, OMDb awards, few-shot examples,
    anti-cliché guard, encourages flowing prose over checklist enumeration."""
    genres_str = ", ".join(movie.genres) if movie.genres else "Unknown"
    keywords_str = ", ".join((movie.keywords or [])[:10]) or "None"
    directors_str = ", ".join(movie.directors) if movie.directors else "Unknown"
    cast_str = ", ".join((movie.cast or [])[:3]) or "Unknown"
    country = derive_country(movie)
    era = derive_era(movie.year)
    awards = movie.awards_text if movie.awards_text and movie.awards_text != "N/A" else "None recorded"
    return [
        {
            "role": "system",
            "content": (
                "You are a cinematic analyst writing concise, evocative descriptions "
                "for film recommendation embeddings. Output plain prose only — no "
                "markdown, no bullet lists, no headers. Avoid clichés like "
                "'masterpiece', 'unforgettable', 'must-see', 'tour de force'. Be "
                "specific and grounded. Always respond in English regardless of the "
                "film's original language."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Film: {movie.title} ({movie.year})\n"
                f"Country: {country}\n"
                f"Era context: {era}\n"
                f"Genres: {genres_str}\n"
                f"Themes/Keywords: {keywords_str}\n"
                f"Director(s): {directors_str}\n"
                f"Lead cast: {cast_str}\n"
                f"Plot synopsis: {movie.overview or 'No plot available.'}\n"
                f"Critical recognition: {awards}\n\n"
                "Style examples to anchor your tone:\n\n"
                "Example 1 (Pan's Labyrinth, 2006):\n"
                "A dreamlike anti-fascist fable set in 1944 post-Civil War Spain. "
                "Del Toro weaves brutal historical violence with baroque dark "
                "fantasy through painterly amber-and-blue cinematography. Pacing "
                "alternates between tense military encounters and contemplative "
                "supernatural reverie. Audience affinity: magic realism, Mexican "
                "Gothic, fairy-tale horror. Mood: haunting, melancholic, mythic, "
                "brutal, transcendent.\n\n"
                "Example 2 (Goodfellas, 1990):\n"
                "A propulsive Scorsese epic tracking three decades of New York mob "
                "life through Henry Hill's amoral voiceover. Frenetic editing, "
                "jukebox needle drops, and Steadicam virtuosity give it a "
                "documentary-meets-rock-opera energy. Pacing escalates from "
                "nostalgic to paranoid as cocaine and betrayal unravel the "
                "brotherhood. Audience affinity: De Palma, The Sopranos, mafia "
                "non-romanticism. Mood: kinetic, decadent, paranoid, cynical, "
                "propulsive.\n\n"
                "Now write a description in this style for the film above. Maximum "
                "80 words. Cover tone, themes, visual/aural style, pacing, and "
                "audience affinity in flowing prose (not a bulleted checklist). "
                "End with 3-5 single-word mood keywords."
            ),
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Combo matrix
# ─────────────────────────────────────────────────────────────────────────────
MODEL_IDS = {
    "scout":   "meta-llama/llama-4-scout-17b-16e-instruct",
    "70b":     "llama-3.3-70b-versatile",
    "oss-120": "openai/gpt-oss-120b",
    "oss-20":  "openai/gpt-oss-20b",
    "8b":      "llama-3.1-8b-instant",
}


def build_combos(with_8b: bool = False) -> list[tuple[str, Callable, str]]:
    """Build the experiment matrix. If with_8b=True, swap the OSS-20 slot for
    Llama 3.1 8B to compare against the weakest fallback in the chain."""
    last_combo = ("8b-new", build_new_prompt, "8b") if with_8b else ("oss20-new", build_new_prompt, "oss-20")
    return [
        ("scout-old",   build_current_prompt, "scout"),
        ("scout-new",   build_new_prompt,     "scout"),
        ("70b-old",     build_current_prompt, "70b"),
        ("70b-new",     build_new_prompt,     "70b"),
        ("oss120-new",  build_new_prompt,     "oss-120"),
        last_combo,
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Groq call with rate-limit handling
# ─────────────────────────────────────────────────────────────────────────────
def _parse_retry_after(error_message: str) -> Optional[float]:
    m = re.search(r"try again in (\d+(?:\.\d+)?)m(\d+(?:\.\d+)?)s", error_message)
    if m:
        return float(m.group(1)) * 60 + float(m.group(2))
    m = re.search(r"try again in (\d+(?:\.\d+)?)s", error_message)
    if m:
        return float(m.group(1))
    return None


def _is_daily_limit(error_message: str) -> bool:
    msg = error_message.lower()
    return "per day" in msg or "(rpd)" in msg or "(tpd)" in msg


async def call_groq(
    client: AsyncOpenAI,
    model_id: str,
    messages: list[dict],
    reasoning_effort: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Returns (description, error_tag). error_tag in {None, 'DailyExhausted', 'RateLimited', 'Error'}.

    Args:
        reasoning_effort: For gpt-oss-* models, controls the internal reasoning
            token budget. One of 'low' / 'medium' / 'high'. Passed via
            `extra_body` so non-gpt-oss models silently ignore it.
    """
    extra_body = {}
    if reasoning_effort and "gpt-oss" in model_id:
        extra_body["reasoning_effort"] = reasoning_effort

    for attempt in range(2):
        try:
            resp = await client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=0.4,
                max_tokens=1000,
                extra_body=extra_body or None,
            )
            text = resp.choices[0].message.content.strip()
            if not text:
                return None, "EmptyResponse"
            if len(text.split()) < 8:
                return text, "VeryShortResponse"
            return text, None
        except Exception as e:
            err = str(e)
            if "429" in err:
                # Verbose log so we can tell RPM vs RPD apart in future runs
                logger.warning(f"    [429 raw] model={model_id} msg={err[:400]}")
                if _is_daily_limit(err):
                    return None, "DailyExhausted"
                wait = _parse_retry_after(err) or 30
                if attempt == 0 and wait < 120:
                    logger.info(f"    RPM 429 on {model_id}, waiting {wait:.0f}s...")
                    await asyncio.sleep(wait + 1)
                    continue
                return None, f"RateLimited({wait:.0f}s)"
            return None, f"Error: {type(e).__name__}: {err[:160]}"
    return None, "FailedAfterRetry"


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
async def main(limit: Optional[int], output: Path, delay: float, with_8b: bool):
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        print("ERROR: GROQ_API_KEY not set in environment")
        sys.exit(1)

    client = AsyncOpenAI(
        api_key=groq_key,
        base_url="https://api.groq.com/openai/v1",
    )

    combos = build_combos(with_8b=with_8b)
    target_ids = EXPERIMENT_FILM_IDS[:limit] if limit else EXPERIMENT_FILM_IDS

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Movie).where(Movie.tmdb_id.in_(target_ids))
        )
        films = list(result.scalars().all())

    if not films:
        print("ERROR: none of the experiment films are in your DB. "
              "Verify EXPERIMENT_FILM_IDS or seed first.")
        sys.exit(1)

    missing = set(target_ids) - {f.tmdb_id for f in films}
    if missing:
        logger.info(f"Skipping {len(missing)} films not in DB: {sorted(missing)}")

    logger.info(f"Running experiment on {len(films)} films × {len(combos)} combos = {len(films) * len(combos)} calls")
    logger.info(f"Combos: {[c[0] for c in combos]}")
    logger.info(f"Pacing: {delay}s between calls. Expected total time: ~{len(films) * len(combos) * (delay + 2.5):.0f}s")

    exhausted: set[str] = set()
    results: dict[int, dict] = {}
    started = time.time()
    success_count = 0

    for f_idx, film in enumerate(films, 1):
        logger.info(f"[{f_idx}/{len(films)}] {film.title} ({film.year})")
        results[film.tmdb_id] = {"film": film, "outputs": {}}
        for combo_name, prompt_fn, model_alias in combos:
            if model_alias in exhausted:
                results[film.tmdb_id]["outputs"][combo_name] = "_(SKIPPED — model exhausted today)_"
                continue
            model_id = MODEL_IDS[model_alias]
            messages = prompt_fn(film)
            desc, err = await call_groq(client, model_id, messages)
            if err == "DailyExhausted":
                exhausted.add(model_alias)
                results[film.tmdb_id]["outputs"][combo_name] = f"_(SKIPPED — {model_alias} daily limit hit)_"
                logger.warning(f"  {combo_name}: {model_alias} exhausted for today, skipping all future {model_alias} calls")
            elif err == "EmptyResponse":
                results[film.tmdb_id]["outputs"][combo_name] = "_(EMPTY RESPONSE — model returned nothing)_"
                logger.warning(f"  {combo_name}: empty response (model refused or failed silently)")
            elif err == "VeryShortResponse":
                # Recorded but flagged — useful signal about model quality
                results[film.tmdb_id]["outputs"][combo_name] = f"⚠️ **Very short ({len(desc.split())} words):** {desc}"
                logger.warning(f"  {combo_name}: ⚠️ very short response ({len(desc.split())} words)")
            elif err:
                results[film.tmdb_id]["outputs"][combo_name] = f"_(ERROR — {err})_"
                logger.warning(f"  {combo_name}: {err}")
            else:
                results[film.tmdb_id]["outputs"][combo_name] = desc
                success_count += 1
                logger.info(f"  {combo_name}: ✓ ({len(desc.split())} words)")
            await asyncio.sleep(delay)

    elapsed = time.time() - started
    logger.info(f"Done in {elapsed:.0f}s. {success_count} successful generations. Exhausted: {sorted(exhausted) or 'none'}")
    total_calls = len(films) * len(combos)

    # Write markdown report
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fp:
        fp.write("# Cinematic Enricher — prompt × model A/B\n\n")
        fp.write(f"- Films tested: **{len(films)}** ({len(missing)} missing from DB skipped)\n")
        fp.write(f"- Combos per film: **{len(combos)}** ({', '.join(c[0] for c in combos)})\n")
        fp.write(f"- Successful generations: **{success_count}** / {total_calls}\n")
        fp.write(f"- Models exhausted: **{sorted(exhausted) or 'none'}**\n")
        fp.write(f"- Elapsed: {elapsed:.0f}s\n\n")
        fp.write("---\n\n")
        for tmdb_id, data in results.items():
            film: Movie = data["film"]
            outputs: dict = data["outputs"]
            fp.write(f"## {film.title} ({film.year})\n\n")
            fp.write(f"- **TMDB ID**: `{tmdb_id}`\n")
            fp.write(f"- **Genres**: {', '.join(film.genres or []) or '_(none)_'}\n")
            fp.write(f"- **Directors**: {', '.join(film.directors or []) or '_(none)_'}\n")
            fp.write(f"- **Country**: {derive_country(film)}\n")
            fp.write(f"- **Era**: {derive_era(film.year)}\n")
            fp.write(f"- **Awards**: {film.awards_text or '_(none)_'}\n")
            stored = film.cinematic_description or ""
            preview = stored[:280] + ("…" if len(stored) > 280 else "")
            fp.write(f"- **Stored (currently in DB)**: {preview or '_(none)_'}\n\n")
            for combo_name, *_ in combos:
                output_text = outputs.get(combo_name, "_(no result)_")
                fp.write(f"### `{combo_name}`\n\n{output_text}\n\n")
            fp.write("---\n\n")

    logger.info(f"Report written to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Test on the first N films (quick dry-run). Default: all 25.")
    parser.add_argument("--output", type=Path, default=Path("experiment_enricher_results.md"), help="Path for the markdown report (relative to /app inside container by default).")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between API calls (rate-limit safety). Default 0.5s.")
    parser.add_argument("--with-8b", action="store_true", help="Swap the OSS-20 slot for Llama 3.1 8B to compare against the weakest fallback.")
    args = parser.parse_args()

    asyncio.run(main(limit=args.limit, output=args.output, delay=args.delay, with_8b=args.with_8b))
