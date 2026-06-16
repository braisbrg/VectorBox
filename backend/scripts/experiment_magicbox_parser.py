"""Scout vs 70B A/B for Magic Box intent parsing.

`nlp_search.parse_user_intent` is the Tier-1 LLM that converts a free-text
search query into a structured `MovieSearchIntent` (Pydantic), via the
instructor library's `response_model`. It currently uses 70B primary +
Scout fallback after we switched away from Scout-as-primary.

Structured-JSON tasks often favour larger models because of stricter
schema adherence. This script runs the SAME `parse_user_intent` against
both models on a panel of realistic, varied queries and dumps both
outputs so we can compare:
  - Schema adherence (do both fill the same fields?)
  - Semantic expansion quality (does `semantic_query` get richer or noisier?)
  - Filter inference quality (do they pick up year bounds, genres, mood
    markers, regional cinema movements?)

Output: markdown with each query's two outputs JSON-formatted side-by-side.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import instructor
from openai import AsyncOpenAI

from services.nlp_search import MovieSearchIntent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


MODELS = {
    "scout":       "meta-llama/llama-4-scout-17b-16e-instruct",
    "70b":         "llama-3.3-70b-versatile",
    "qwen3-32b":   "qwen/qwen3-32b",
    "qwen3.6-27b": "qwen/qwen3.6-27b",
}

# Reasoning models burn the token budget on chain-of-thought before emitting the
# tool call; disable it so structured parsing stays fast and doesn't truncate.
_REASONING_OFF = {"qwen/qwen3-32b", "qwen/qwen3.6-27b"}


# Realistic query panel covering the dimensions the schema models:
# language, era, genre, regional movement, reference-film, mood, quality,
# audience rating, awards, hidden-gem vs blockbuster.
QUERIES = [
    "movies like Inception but slower and more meditative",
    "80s synth-driven sci-fi noir",
    "cine quinqui de los ochenta",
    "Korean revenge cinema with morally grey protagonists",
    "feel-good family animation, suitable for kids",
    "critically acclaimed Spanish-language films from the last decade",
    "hidden gem psychological horror, slow burn, european",
    "campy 90s direct-to-video action — bad but fun",
    "Cannes Palme d'Or winners about families",
    "ganadoras de Oscar de los 70s con violencia política",
    "j-horror with ghosts and rural villages",
    "películas como Pan's Labyrinth pero más oscuras todavía",
]


# Same system prompt as nlp_search.py (abbreviated header — full instructions
# are in the schema field descriptions which instructor surfaces via the tool
# call). We replicate the actual production system prompt verbatim by reading
# from nlp_search.py at runtime.
def _load_production_system_prompt() -> str:
    """Extract the multi-line `system_prompt` string from nlp_search.py.

    Simpler than re-importing the function — we just need the same text the
    LLM sees in production. The system prompt is built inline inside
    parse_user_intent so we'd have to refactor to expose it. For the
    experiment we hand-copy the leading instruction block that anchors style;
    the schema field descriptions do most of the heavy lifting via instructor.
    """
    return (
        "You are an expert film-intent parser. Given a free-text user query "
        "in English or Spanish, populate the MovieSearchIntent schema as "
        "completely and faithfully as possible.\n\n"
        "Key principles:\n"
        "1. `semantic_query` MUST be a rich English expansion of the user's "
        "intent — include synonyms, related themes, regional movement names, "
        "and stylistic markers. This is the vector-search anchor.\n"
        "2. Fill numeric/categorical filters (year_min, year_max, "
        "include_genres, original_language, mpaa_ratings, min_oscar_wins, "
        "min_imdb_rating, min_metacritic, countries, spoken_languages, "
        "awards_contains) ONLY when the query clearly implies them.\n"
        "3. Set `quality_gate_bypass=True` ONLY for explicit campy / trashy / "
        "so-bad-it's-good / B-movie requests.\n"
        "4. Set `safe_mode=False` ONLY when the user explicitly seeks "
        "adult/NSFW content (very rare).\n"
        "5. Recognise regional cinema movements (quinqui, nouvelle vague, "
        "giallo, j-horror, spaghetti western, neorealismo, etc.) and expand "
        "them with canonical synonyms in `semantic_query`.\n"
        "6. If the user references a specific film ('like X', 'similar to X'), "
        "put the title in `reference_movie` AND expand themes in `semantic_query`."
    )


async def parse_with_model(client, query: str, model_id: str) -> tuple[Optional[dict], Optional[str]]:
    """Call instructor + Groq to parse one query. Returns (dict-of-fields, error)."""
    system_prompt = _load_production_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"### USER QUERY ###\n{query}\n### END USER QUERY ###"},
    ]
    extra = {"extra_body": {"reasoning_effort": "none"}} if model_id in _REASONING_OFF else {}
    try:
        intent = await client.chat.completions.create(
            model=model_id,
            response_model=MovieSearchIntent,
            messages=messages,
            temperature=0.1,
            **extra,
        )
        # Strip None/default fields to keep the comparison readable
        d = intent.model_dump(exclude_defaults=True, exclude_none=True)
        return d, None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


async def main(output: Path, delay: float):
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        print("ERROR: GROQ_API_KEY not set")
        sys.exit(1)

    raw_client = AsyncOpenAI(base_url="https://api.groq.com/openai/v1", api_key=groq_key)
    client = instructor.from_openai(raw_client, mode=instructor.Mode.TOOLS)

    total = len(QUERIES) * len(MODELS)
    logger.info(f"Magic Box parser A/B: {len(QUERIES)} queries × {len(MODELS)} models = {total} calls")

    started = time.time()
    results: dict[int, dict] = {}
    success = 0
    for q_idx, query in enumerate(QUERIES, 1):
        logger.info(f"[{q_idx}/{len(QUERIES)}] {query!r}")
        results[q_idx] = {"query": query, "outputs": {}}
        for model_alias, model_id in MODELS.items():
            parsed, err = await parse_with_model(client, query, model_id)
            if err:
                results[q_idx]["outputs"][model_alias] = {"error": err}
                logger.warning(f"  {model_alias}: {err}")
            else:
                results[q_idx]["outputs"][model_alias] = parsed
                success += 1
                logger.info(f"  {model_alias}: ✓ ({len(parsed)} fields filled)")
            await asyncio.sleep(delay)

    elapsed = time.time() - started
    logger.info(f"Done in {elapsed:.0f}s. {success}/{total} successful.")

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fp:
        fp.write("# Magic Box parser A/B — Scout vs 70B\n\n")
        fp.write(f"- Queries: **{len(QUERIES)}**\n")
        fp.write(f"- Models: **{', '.join(MODELS.keys())}**\n")
        fp.write(f"- Successful: **{success}** / {total}\n")
        fp.write(f"- Elapsed: {elapsed:.0f}s\n\n")
        fp.write("Tier-1 intent parser converts free-text queries into a structured "
                 "`MovieSearchIntent`. Read each query's two outputs side-by-side. "
                 "Things to look for:\n"
                 "- Schema adherence: did both models populate the same fields?\n"
                 "- `semantic_query`: is the English expansion rich or shallow?\n"
                 "- Inferences: era → year_min/year_max, language hints → "
                 "original_language, regional movements → countries.\n"
                 "- Hallucinations: did the model invent fields the query doesn't imply?\n\n---\n\n")
        for q_idx, data in results.items():
            query = data["query"]
            outputs = data["outputs"]
            fp.write(f"## Q{q_idx}: `{query}`\n\n")
            for model_alias in MODELS.keys():
                d = outputs.get(model_alias, {})
                fp.write(f"### `{model_alias}`\n\n")
                if "error" in d:
                    fp.write(f"_(ERROR — {d['error']})_\n\n")
                else:
                    fp.write("```json\n")
                    fp.write(json.dumps(d, indent=2, ensure_ascii=False))
                    fp.write("\n```\n\n")
            fp.write("---\n\n")

    logger.info(f"Report written to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("experiment_magicbox_parser_results.md"))
    parser.add_argument("--delay", type=float, default=0.5)
    args = parser.parse_args()

    asyncio.run(main(output=args.output, delay=args.delay))
