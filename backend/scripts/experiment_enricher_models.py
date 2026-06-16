"""Model sweep for the WINNING enricher prompt (V2-nameban).

The prompt-variant experiment (2026-05) settled the prompt: V2-nameban (rich
context + few-shot + ban on naming title/director/cast). This script holds that
prompt fixed and sweeps it across every viable Groq free-tier model as of the
live /v1/models listing (2026-06), INCLUDING the two Qwen models that weren't
in the original runs:

  scout        meta-llama/llama-4-scout-17b-16e-instruct  (preview)
  70b          llama-3.3-70b-versatile                     (current chain primary fallback)
  qwen3-32b    qwen/qwen3-32b                              (NEW — reasoning, needs effort=none)
  qwen3.6-27b  qwen/qwen3.6-27b                            (NEW — reasoning, needs effort=none)
  gpt-oss-120b openai/gpt-oss-120b                         (reasoning, effort=low per reasoning expt)

Reasoning models (`qwen*`, `gpt-oss-*`) emit chain-of-thought. Qwen leaks it as
inline `<think>...</think>` in content unless reasoning_effort='none'; gpt-oss
empties out at effort='high' (use 'low'). We set the right effort per model AND
strip `<think>` blocks defensively.

Output: markdown, all models side-by-side per film. No DB writes.
"""
import argparse
import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import AsyncOpenAI
from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie

from scripts.experiment_enricher_prompt import EXPERIMENT_FILM_IDS, derive_era, derive_country
from scripts.experiment_enricher_prompts_v2 import build_v2_nameban

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


# (alias, model_id, reasoning_effort or None)
MODELS = [
    ("scout",        "meta-llama/llama-4-scout-17b-16e-instruct", None),
    ("70b",          "llama-3.3-70b-versatile",                   None),
    ("qwen3-32b",    "qwen/qwen3-32b",                            "none"),
    ("qwen3.6-27b",  "qwen/qwen3.6-27b",                          "none"),
    ("gpt-oss-120b", "openai/gpt-oss-120b",                       "low"),
]

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Remove any leaked <think>...</think> chain-of-thought, plus a dangling
    unclosed <think> (model ran out of budget mid-thought)."""
    text = _THINK_RE.sub("", text)
    if "<think>" in text.lower():
        # unclosed think block — drop everything from it onward
        idx = text.lower().rfind("<think>")
        text = text[:idx]
    return text.strip()


async def call_model(client, model_id, messages, effort) -> tuple[Optional[str], Optional[str]]:
    extra_body = {}
    if effort:
        extra_body["reasoning_effort"] = effort
    for attempt in range(2):
        try:
            resp = await client.chat.completions.create(
                model=model_id, messages=messages, temperature=0.4,
                max_tokens=1200, extra_body=extra_body or None,
            )
            raw = (resp.choices[0].message.content or "").strip()
            text = _strip_think(raw)
            if not text:
                return None, "EmptyResponse"
            if len(text.split()) < 8:
                return text, "VeryShort"
            return text, None
        except Exception as e:
            err = str(e)
            if "429" in err:
                m = re.search(r"try again in (\d+(?:\.\d+)?)s", err)
                wait = float(m.group(1)) if m else 30
                if attempt == 0 and wait < 90 and "per day" not in err.lower():
                    logger.info(f"    RPM 429 {model_id}, waiting {wait:.0f}s")
                    await asyncio.sleep(wait + 1)
                    continue
                return None, "DailyExhausted" if "per day" in err.lower() else f"RateLimited"
            return None, f"Error: {type(e).__name__}: {err[:140]}"
    return None, "FailedAfterRetry"


async def main(limit: int, output: Path, delay: float):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        print("ERROR: GROQ_API_KEY not set"); sys.exit(1)
    client = AsyncOpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")

    target_ids = EXPERIMENT_FILM_IDS[:limit] if limit else EXPERIMENT_FILM_IDS
    async with AsyncSessionLocal() as db:
        films = list((await db.execute(select(Movie).where(Movie.tmdb_id.in_(target_ids)))).scalars().all())
    # preserve the curated panel order
    order = {tid: i for i, tid in enumerate(target_ids)}
    films.sort(key=lambda f: order.get(f.tmdb_id, 999))

    total = len(films) * len(MODELS)
    logger.info(f"Enricher model sweep (prompt=V2-nameban): {len(films)} films × {len(MODELS)} models = {total} calls")

    results: dict[int, dict] = {}
    started = time.time()
    success = 0
    exhausted: set[str] = set()
    word_counts: dict[str, list] = {a: [] for a, _, _ in MODELS}

    for i, film in enumerate(films, 1):
        logger.info(f"[{i}/{len(films)}] {film.title} ({film.year})")
        results[film.tmdb_id] = {"film": film, "outputs": {}}
        messages = build_v2_nameban(film)
        for alias, model_id, effort in MODELS:
            if alias in exhausted:
                results[film.tmdb_id]["outputs"][alias] = "_(SKIPPED — daily limit)_"
                continue
            desc, err = await call_model(client, model_id, messages, effort)
            if err == "DailyExhausted":
                exhausted.add(alias)
                results[film.tmdb_id]["outputs"][alias] = "_(SKIPPED — daily limit hit)_"
                logger.warning(f"  {alias}: exhausted")
            elif err in ("EmptyResponse",):
                results[film.tmdb_id]["outputs"][alias] = "_(EMPTY RESPONSE)_"
                logger.warning(f"  {alias}: empty")
            elif err == "VeryShort":
                results[film.tmdb_id]["outputs"][alias] = f"⚠️ Very short: {desc}"
            elif err:
                results[film.tmdb_id]["outputs"][alias] = f"_(ERROR — {err})_"
                logger.warning(f"  {alias}: {err}")
            else:
                results[film.tmdb_id]["outputs"][alias] = desc
                success += 1
                word_counts[alias].append(len(desc.split()))
                logger.info(f"  {alias}: ✓ ({len(desc.split())}w)")
            await asyncio.sleep(delay)

    elapsed = time.time() - started
    logger.info(f"Done in {elapsed:.0f}s. {success}/{total} successful.")

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fp:
        fp.write("# Enricher MODEL sweep — winning prompt (V2-nameban) fixed\n\n")
        fp.write(f"- Films: **{len(films)}**\n")
        fp.write(f"- Models: **{', '.join(a for a, _, _ in MODELS)}**\n")
        fp.write(f"- Successful: **{success}** / {total}\n")
        fp.write(f"- Exhausted: {sorted(exhausted) or 'none'}\n")
        fp.write(f"- Elapsed: {elapsed:.0f}s\n\n")
        fp.write("Avg word count (target ~70-80, signals checklist-vs-prose discipline):\n\n")
        for a, _, _ in MODELS:
            wc = word_counts[a]
            avg = sum(wc) / len(wc) if wc else 0
            fp.write(f"- `{a}`: {avg:.0f} words (n={len(wc)})\n")
        fp.write("\n---\n\n")
        for tmdb_id, data in results.items():
            film = data["film"]
            fp.write(f"## {film.title} ({film.year})\n\n")
            fp.write(f"- Genres: {', '.join(film.genres or []) or '_(none)_'} | Country: {derive_country(film)} | Era: {derive_era(film.year)}\n")
            stored = (film.cinematic_description or "")[:240]
            fp.write(f"- **Stored in DB**: {stored}…\n\n")
            for alias, _, _ in MODELS:
                fp.write(f"### `{alias}`\n\n{data['outputs'].get(alias, '_(no result)_')}\n\n")
            fp.write("---\n\n")
    logger.info(f"Report → {output}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--output", type=Path, default=Path("experiment_enricher_models.md"))
    p.add_argument("--delay", type=float, default=0.4)
    a = p.parse_args()
    asyncio.run(main(limit=a.limit, output=a.output, delay=a.delay))
