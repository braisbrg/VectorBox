"""Prompt-variant A/B for cinematic_description enrichment (paso 3).

Locks the model to GPT-OSS-120B (best from previous experiment, most quota
remaining today) and varies ONLY the prompt across 6 variants:

  V0 current        — production checklist prompt
  V1 new-rich       — prompt from previous experiment (country/era/awards/few-shot)
  V2 new-nameban    — V1 + explicit ban on naming director/cast/title in OUTPUT
  V3 minimal-vibes  — strip inputs to genres/keywords/overview; vibes-only
  V4 keyword-anchored — instruct LLM to use keywords as the spine of prose
  V5 no-mood-tail   — V2 without the "End with 3-5 mood keywords" instruction

Reuses the existing script's helpers (EXPERIMENT_FILM_IDS, derive_era,
derive_country, call_groq, build_current_prompt, build_new_prompt).

Output: markdown with all 6 variants side-by-side per film. No DB writes.
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import AsyncOpenAI
from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie

# Reuse from the model-comparison script
from scripts.experiment_enricher_prompt import (
    EXPERIMENT_FILM_IDS,
    derive_era,
    derive_country,
    build_current_prompt,
    build_new_prompt,
    call_groq,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


MODEL_ID = "openai/gpt-oss-120b"


# ─────────────────────────────────────────────────────────────────────────────
# Shared name-ban instruction (used by V2, V4, V5)
# ─────────────────────────────────────────────────────────────────────────────
NAME_BAN = (
    "STRICT RULE: Do NOT mention the film's title, any director name, any "
    "actor name, or any character name in your output. Refer to the "
    "filmmaker's authorial style without naming them. The output must be "
    "describable for ANY film of similar themes — no identity tokens that "
    "would leak between unrelated films sharing a cast member."
)


# ─────────────────────────────────────────────────────────────────────────────
# V2 — new-rich + name-ban
# ─────────────────────────────────────────────────────────────────────────────
def build_v2_nameban(movie: Movie) -> list[dict]:
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
                "'masterpiece', 'unforgettable', 'must-see', 'tour de force'. "
                "Always respond in English regardless of the film's original language.\n\n"
                + NAME_BAN
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
                f"Director(s) (for context only — DO NOT name): {directors_str}\n"
                f"Lead cast (for context only — DO NOT name): {cast_str}\n"
                f"Plot synopsis: {movie.overview or 'No plot available.'}\n"
                f"Critical recognition: {awards}\n\n"
                "Style examples (note: the examples use names — yours must NOT):\n\n"
                "Example 1 (a 1944 Spanish dark-fantasy fable):\n"
                "A dreamlike anti-fascist fable set in post-Civil War Spain, weaving "
                "brutal historical violence with baroque dark fantasy through painterly "
                "amber-and-blue cinematography. Pacing alternates between tense military "
                "encounters and contemplative supernatural reverie. Audience affinity: "
                "magic realism, Mexican Gothic, fairy-tale horror. Mood: haunting, "
                "melancholic, mythic, brutal, transcendent.\n\n"
                "Example 2 (a 1990s New York mob epic):\n"
                "A propulsive epic tracking three decades of New York mob life through "
                "an amoral first-person voiceover. Frenetic editing, jukebox needle "
                "drops, and Steadicam virtuosity give it a documentary-meets-rock-opera "
                "energy. Pacing escalates from nostalgic to paranoid as cocaine and "
                "betrayal unravel the brotherhood. Audience affinity: mafia "
                "non-romanticism, ensemble crime cinema. Mood: kinetic, decadent, "
                "paranoid, cynical, propulsive.\n\n"
                "Now write a description in this style. Maximum 80 words. Cover tone, "
                "themes, visual/aural style, pacing, and audience affinity in flowing "
                "prose. End with 3-5 single-word mood keywords. No names, no title."
            ),
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# V3 — minimal-vibes: strip context, vibes-only
# ─────────────────────────────────────────────────────────────────────────────
def build_v3_minimal(movie: Movie) -> list[dict]:
    genres_str = ", ".join(movie.genres) if movie.genres else "Unknown"
    keywords_str = ", ".join((movie.keywords or [])[:10]) or "None"
    return [
        {
            "role": "system",
            "content": (
                "You are a film-vibe analyst. Output plain prose only — no markdown, "
                "no bullets, no headers. Avoid clichés ('masterpiece', 'unforgettable'). "
                "Always respond in English.\n\n"
                + NAME_BAN
            ),
        },
        {
            "role": "user",
            "content": (
                f"Genres: {genres_str}\n"
                f"Themes/Keywords: {keywords_str}\n"
                f"Plot synopsis: {movie.overview or 'No plot available.'}\n\n"
                "Capture the VIBE and THEMES of this film only. Do NOT summarize plot. "
                "Do NOT mention any name (title, director, actor, character). "
                "Focus on: tone, mood, themes, visual/aural texture, pacing energy, and "
                "what kind of viewer it appeals to. Plain prose, ~80 words. "
                "End with 3-5 single-word mood keywords."
            ),
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# V4 — keyword-anchored: use keywords as spine
# ─────────────────────────────────────────────────────────────────────────────
def build_v4_keyword_anchored(movie: Movie) -> list[dict]:
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
                "You are a cinematic analyst. Output plain prose only — no markdown, "
                "no bullets. Avoid clichés ('masterpiece', 'unforgettable'). "
                "Always respond in English.\n\n"
                + NAME_BAN
            ),
        },
        {
            "role": "user",
            "content": (
                f"Film context (do not name people or title): {movie.title} ({movie.year}), "
                f"{country}, {era}\n"
                f"Genres: {genres_str}\n"
                f"KEY THEMES (use these as the spine of your description): {keywords_str}\n"
                f"Director context (style only, do not name): {directors_str}\n"
                f"Cast context (do not name): {cast_str}\n"
                f"Plot synopsis: {movie.overview or 'No plot available.'}\n"
                f"Critical recognition: {awards}\n\n"
                "TASK: Build your description by expanding the KEY THEMES above into "
                "evocative prose. Do NOT introduce themes or moods absent from the "
                "themes list. Use them as anchors; the rest of the prose explains them. "
                "Cover tone, visual/aural style, pacing in flowing prose. ~80 words. "
                "No names. End with 3-5 single-word mood keywords (which should also "
                "reflect the themes above)."
            ),
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# V5 — no mood-keyword tail
# ─────────────────────────────────────────────────────────────────────────────
def build_v5_no_mood_tail(movie: Movie) -> list[dict]:
    # Start from V2 then replace the closing instruction.
    msgs = build_v2_nameban(movie)
    user_content = msgs[1]["content"]
    # Replace the mood-keyword instruction with a flowing-sentence one
    user_content = user_content.replace(
        "End with 3-5 single-word mood keywords. No names, no title.",
        "End with a single flowing sentence summarizing the mood, NOT an isolated "
        "keyword list. No names, no title.",
    )
    msgs[1]["content"] = user_content
    return msgs


# ─────────────────────────────────────────────────────────────────────────────
# Variant matrix
# ─────────────────────────────────────────────────────────────────────────────
VARIANTS: list[tuple[str, Callable]] = [
    ("V0-current",         build_current_prompt),
    ("V1-new-rich",        build_new_prompt),
    ("V2-new-nameban",     build_v2_nameban),
    ("V3-minimal-vibes",   build_v3_minimal),
    ("V4-keyword-anchored", build_v4_keyword_anchored),
    ("V5-no-mood-tail",    build_v5_no_mood_tail),
]


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
async def main(limit: Optional[int], output: Path, delay: float, reasoning_effort: Optional[str]):
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        print("ERROR: GROQ_API_KEY not set in environment")
        sys.exit(1)

    client = AsyncOpenAI(
        api_key=groq_key,
        base_url="https://api.groq.com/openai/v1",
    )

    target_ids = EXPERIMENT_FILM_IDS[:limit] if limit else EXPERIMENT_FILM_IDS

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Movie).where(Movie.tmdb_id.in_(target_ids))
        )
        films = list(result.scalars().all())

    if not films:
        print("ERROR: none of the experiment films are in your DB.")
        sys.exit(1)

    missing = set(target_ids) - {f.tmdb_id for f in films}
    if missing:
        logger.info(f"Skipping {len(missing)} films not in DB: {sorted(missing)}")

    total_calls = len(films) * len(VARIANTS)
    logger.info(f"Running prompt experiment on {len(films)} films × {len(VARIANTS)} variants = {total_calls} calls")
    logger.info(f"Model: {MODEL_ID} (fixed)")
    logger.info(f"Variants: {[v[0] for v in VARIANTS]}")
    logger.info(f"Reasoning effort: {reasoning_effort or '(default)'}")
    logger.info(f"Pacing: {delay}s. Expected total time: ~{total_calls * (delay + 3.0):.0f}s")

    results: dict[int, dict] = {}
    started = time.time()
    success_count = 0
    exhausted_flag = False

    for f_idx, film in enumerate(films, 1):
        logger.info(f"[{f_idx}/{len(films)}] {film.title} ({film.year})")
        results[film.tmdb_id] = {"film": film, "outputs": {}}
        for variant_name, prompt_fn in VARIANTS:
            if exhausted_flag:
                results[film.tmdb_id]["outputs"][variant_name] = "_(SKIPPED — oss-120 daily limit hit)_"
                continue
            messages = prompt_fn(film)
            desc, err = await call_groq(client, MODEL_ID, messages, reasoning_effort=reasoning_effort)
            if err == "DailyExhausted":
                exhausted_flag = True
                results[film.tmdb_id]["outputs"][variant_name] = "_(SKIPPED — oss-120 daily limit hit)_"
                logger.warning(f"  {variant_name}: oss-120 exhausted for today, aborting remaining calls")
            elif err == "EmptyResponse":
                results[film.tmdb_id]["outputs"][variant_name] = "_(EMPTY RESPONSE — model returned nothing)_"
                logger.warning(f"  {variant_name}: empty response")
            elif err == "VeryShortResponse":
                results[film.tmdb_id]["outputs"][variant_name] = f"⚠️ **Very short ({len(desc.split())} words):** {desc}"
                logger.warning(f"  {variant_name}: ⚠️ very short response ({len(desc.split())} words)")
            elif err:
                results[film.tmdb_id]["outputs"][variant_name] = f"_(ERROR — {err})_"
                logger.warning(f"  {variant_name}: {err}")
            else:
                results[film.tmdb_id]["outputs"][variant_name] = desc
                success_count += 1
                logger.info(f"  {variant_name}: ✓ ({len(desc.split())} words)")
            await asyncio.sleep(delay)

    elapsed = time.time() - started
    logger.info(f"Done in {elapsed:.0f}s. {success_count} successful generations. Exhausted: {exhausted_flag}")

    # Write markdown report
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fp:
        fp.write("# Prompt-variant A/B (model fixed = GPT-OSS-120B)\n\n")
        fp.write(f"- Model: `{MODEL_ID}` (fixed across all variants)\n")
        fp.write(f"- Films tested: **{len(films)}** ({len(missing)} missing from DB skipped)\n")
        fp.write(f"- Variants: **{len(VARIANTS)}** ({', '.join(v[0] for v in VARIANTS)})\n")
        fp.write(f"- Successful generations: **{success_count}** / {total_calls}\n")
        fp.write(f"- Daily limit hit: {'YES (run truncated)' if exhausted_flag else 'no'}\n")
        fp.write(f"- Reasoning effort: `{reasoning_effort or 'default'}`\n")
        fp.write(f"- Elapsed: {elapsed:.0f}s\n\n")
        fp.write("**Variant summary:**\n")
        fp.write("- `V0-current`: production checklist prompt (baseline)\n")
        fp.write("- `V1-new-rich`: previous experiment's new prompt (country/era/awards/few-shot)\n")
        fp.write("- `V2-new-nameban`: V1 + ban on naming director/cast/title in OUTPUT\n")
        fp.write("- `V3-minimal-vibes`: strip inputs (no director/cast/awards/country/era), vibes-only\n")
        fp.write("- `V4-keyword-anchored`: V2 minus few-shot; instruct LLM to use keywords as spine\n")
        fp.write("- `V5-no-mood-tail`: V2 minus the isolated mood-keyword tail (single closing sentence instead)\n\n")
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
            for variant_name, _ in VARIANTS:
                output_text = outputs.get(variant_name, "_(no result)_")
                fp.write(f"### `{variant_name}`\n\n{output_text}\n\n")
            fp.write("---\n\n")

    logger.info(f"Report written to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Test on the first N films (quick dry-run). Default: all 24.")
    parser.add_argument("--output", type=Path, default=Path("experiment_prompts_v2_results.md"), help="Path for the markdown report.")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between API calls (rate-limit safety). Default 0.5s.")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default=None,
                        help="Pass reasoning_effort to gpt-oss models (controls internal CoT budget). Default: model default.")
    args = parser.parse_args()

    asyncio.run(main(limit=args.limit, output=args.output, delay=args.delay, reasoning_effort=args.reasoning_effort))
