"""Reasoning-effort A/B for GPT-OSS-120B on cinematic_description.

Locks both model (GPT-OSS-120B) and prompt (V2-new-nameban, the most stable
in the variant experiment) and varies ONLY `reasoning_effort` across
three levels: low / medium / high.

`reasoning_effort` is a gpt-oss-specific parameter that controls how much
internal chain-of-thought the model emits before producing visible output.
Hypothesis: many empty responses observed in the variant experiment were
caused by the model over-thinking and exhausting its max_tokens budget on
internal CoT. Lower effort = less reasoning = more budget for visible text.

Sample: 8 films chosen for diversity (art-house, mainstream, classic,
Spanish, Asian, sci-fi, horror, animation). Small panel keeps token cost
modest (~24 calls) so we don't burn TPD before drawing a conclusion.

Output: markdown side-by-side per film with the three effort levels.
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import AsyncOpenAI
from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie

from scripts.experiment_enricher_prompt import (
    derive_era,
    derive_country,
    call_groq,
)
from scripts.experiment_enricher_prompts_v2 import build_v2_nameban

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


MODEL_ID = "openai/gpt-oss-120b"

# 8 films across genre/era/origin (subset of the 24-film panel)
SAMPLE_FILMS = [
    129,      # Spirited Away (animation, Japan)
    1417,     # Pan's Labyrinth (art-house, Spain/Mexico)
    27205,    # Inception (mainstream sci-fi, US)
    238,      # The Godfather (classic crime, US)
    372058,   # Your Name (anime, Japan)
    11770,    # Mar adentro (Spanish drama)
    694,      # The Shining (horror, US)
    78,       # Blade Runner (sci-fi, US)
]

REASONING_LEVELS = ["low", "medium", "high"]


async def main(output: Path, delay: float):
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        print("ERROR: GROQ_API_KEY not set in environment")
        sys.exit(1)

    client = AsyncOpenAI(
        api_key=groq_key,
        base_url="https://api.groq.com/openai/v1",
    )

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Movie).where(Movie.tmdb_id.in_(SAMPLE_FILMS))
        )
        films = list(result.scalars().all())

    if not films:
        print("ERROR: no sample films found in DB")
        sys.exit(1)

    missing = set(SAMPLE_FILMS) - {f.tmdb_id for f in films}
    if missing:
        logger.warning(f"Films not in DB (skipped): {sorted(missing)}")

    total_calls = len(films) * len(REASONING_LEVELS)
    logger.info(f"Reasoning-effort experiment: {len(films)} films × {len(REASONING_LEVELS)} efforts = {total_calls} calls")
    logger.info(f"Model: {MODEL_ID} | Prompt: V2-new-nameban (fixed)")
    logger.info(f"Pacing: {delay}s. Expected total time: ~{total_calls * (delay + 4.0):.0f}s")

    results: dict[int, dict] = {}
    started = time.time()
    success_count = 0
    exhausted_flag = False

    for f_idx, film in enumerate(films, 1):
        logger.info(f"[{f_idx}/{len(films)}] {film.title} ({film.year})")
        results[film.tmdb_id] = {"film": film, "outputs": {}}
        for effort in REASONING_LEVELS:
            if exhausted_flag:
                results[film.tmdb_id]["outputs"][effort] = "_(SKIPPED — oss-120 daily limit hit)_"
                continue
            messages = build_v2_nameban(film)
            desc, err = await call_groq(client, MODEL_ID, messages, reasoning_effort=effort)
            if err == "DailyExhausted":
                exhausted_flag = True
                results[film.tmdb_id]["outputs"][effort] = "_(SKIPPED — oss-120 daily limit hit)_"
                logger.warning(f"  effort={effort}: oss-120 exhausted, aborting remaining")
            elif err == "EmptyResponse":
                results[film.tmdb_id]["outputs"][effort] = "_(EMPTY RESPONSE)_"
                logger.warning(f"  effort={effort}: empty response")
            elif err == "VeryShortResponse":
                results[film.tmdb_id]["outputs"][effort] = f"⚠️ **Very short ({len(desc.split())} words):** {desc}"
                logger.warning(f"  effort={effort}: ⚠️ very short ({len(desc.split())} words)")
            elif err:
                results[film.tmdb_id]["outputs"][effort] = f"_(ERROR — {err})_"
                logger.warning(f"  effort={effort}: {err}")
            else:
                results[film.tmdb_id]["outputs"][effort] = desc
                success_count += 1
                logger.info(f"  effort={effort}: ✓ ({len(desc.split())} words)")
            await asyncio.sleep(delay)

    elapsed = time.time() - started
    logger.info(f"Done in {elapsed:.0f}s. {success_count} successful generations. Exhausted: {exhausted_flag}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fp:
        fp.write("# Reasoning-effort A/B for GPT-OSS-120B\n\n")
        fp.write(f"- Model: `{MODEL_ID}` (fixed)\n")
        fp.write(f"- Prompt: `V2-new-nameban` (fixed)\n")
        fp.write(f"- Sample: **{len(films)}** films\n")
        fp.write(f"- Efforts: **{', '.join(REASONING_LEVELS)}**\n")
        fp.write(f"- Successful: **{success_count}** / {total_calls}\n")
        fp.write(f"- Daily limit hit: {'YES (run truncated)' if exhausted_flag else 'no'}\n")
        fp.write(f"- Elapsed: {elapsed:.0f}s\n\n")
        fp.write("Hypothesis under test: the empty responses seen in the prompt experiment "
                 "were caused by the model exhausting its `max_tokens` budget on internal "
                 "chain-of-thought reasoning. Lower `reasoning_effort` should reduce empties.\n\n")
        fp.write("---\n\n")
        for tmdb_id, data in results.items():
            film: Movie = data["film"]
            outputs: dict = data["outputs"]
            fp.write(f"## {film.title} ({film.year})\n\n")
            fp.write(f"- **TMDB ID**: `{tmdb_id}`\n")
            fp.write(f"- **Genres**: {', '.join(film.genres or []) or '_(none)_'}\n")
            fp.write(f"- **Directors**: {', '.join(film.directors or []) or '_(none)_'}\n")
            fp.write(f"- **Country**: {derive_country(film)}\n")
            fp.write(f"- **Era**: {derive_era(film.year)}\n\n")
            for effort in REASONING_LEVELS:
                output_text = outputs.get(effort, "_(no result)_")
                fp.write(f"### `reasoning_effort={effort}`\n\n{output_text}\n\n")
            fp.write("---\n\n")

    logger.info(f"Report written to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("experiment_reasoning_effort_results.md"))
    parser.add_argument("--delay", type=float, default=0.5)
    args = parser.parse_args()

    asyncio.run(main(output=args.output, delay=args.delay))
