"""
backfill_descriptions.py — Backfill cinematic_description for already-enriched movies.

Targets movies with has_enriched_embedding=True AND cinematic_description IS NULL.
Only saves the LLM-generated text — does NOT regenerate embeddings.

Usage:
    docker compose exec backend python scripts/backfill_descriptions.py [--limit N] [--dry-run]
"""
import asyncio
import os
import sys
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from config import AsyncSessionLocal
from models.database import Movie

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")


async def backfill(limit: int = None, dry_run: bool = False):
    from openai import AsyncOpenAI
    from services.cinematic_enricher import generate_cinematic_description, DailyLimitExhausted

    gemini_key = os.environ.get("GEMINI_API_KEY")
    groq_key = os.environ.get("GROQ_API_KEY")

    if groq_key:
        logger.info("Using Groq for LLM descriptions")
        llm_client = AsyncOpenAI(
            api_key=groq_key,
            base_url="https://api.groq.com/openai/v1",
            max_retries=0,
        )
        batch_size = 10
        batch_delay = 2.0
    elif gemini_key:
        logger.info("Using Gemini for LLM descriptions")
        llm_client = AsyncOpenAI(
            api_key=gemini_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        batch_size = 30
        batch_delay = 0.3
    else:
        logger.error("Neither GEMINI_API_KEY nor GROQ_API_KEY is set. Aborting.")
        return

    async with AsyncSessionLocal() as db:
        # Count total
        count_q = select(func.count(Movie.id)).where(
            Movie.has_enriched_embedding.is_(True),
            Movie.cinematic_description.is_(None),
        )
        total = (await db.execute(count_q)).scalar_one()
        logger.info(f"Found {total} enriched movies missing cinematic_description")

        if total == 0:
            print("\n✓ All enriched movies already have descriptions saved.")
            return

        if dry_run:
            print(f"\n[DRY RUN] Would backfill {min(limit, total) if limit else total} movies. Exiting.")
            return

        # Fetch candidates
        query = select(Movie).where(
            Movie.has_enriched_embedding.is_(True),
            Movie.cinematic_description.is_(None),
        )
        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        candidates = result.scalars().all()

        saved = 0
        errors = 0
        t0 = time.time()

        for batch_start in range(0, len(candidates), batch_size):
            batch = candidates[batch_start:batch_start + batch_size]

            for movie in batch:
                try:
                    description, model_used = await generate_cinematic_description(
                        title=movie.title or "",
                        overview=movie.overview or "",
                        genres=movie.genres or [],
                        keywords=movie.keywords or [],
                        directors=movie.directors or [],
                        cast=movie.cast or [],
                        year=movie.year or 0,
                        groq_client=llm_client,
                    )

                    if description:
                        movie.cinematic_description = description
                        saved += 1
                        if saved % 25 == 0:
                            logger.info(f"Progress: {saved}/{len(candidates)} saved")
                    else:
                        logger.warning(f"Empty description for {movie.title} (tmdb={movie.tmdb_id})")
                        errors += 1

                except DailyLimitExhausted:
                    logger.error("Daily LLM limit exhausted. Committing progress and stopping.")
                    await db.commit()
                    break
                except Exception as e:
                    logger.error(f"Failed for {movie.title} (tmdb={movie.tmdb_id}): {e}")
                    errors += 1
            else:
                # Commit per batch
                await db.commit()
                if batch_start + batch_size < len(candidates):
                    await asyncio.sleep(batch_delay)
                continue
            break  # DailyLimitExhausted broke inner loop

        # Final commit
        await db.commit()
        elapsed = time.time() - t0

        print(f"\n=== Backfill Summary ===")
        print(f"Total candidates:  {len(candidates)}")
        print(f"Saved:             {saved}")
        print(f"Errors:            {errors}")
        print(f"Time:              {elapsed:.1f}s")
        print(f"Remaining NULL:    {len(candidates) - saved - errors}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backfill cinematic_description for enriched movies")
    parser.add_argument("--limit", type=int, default=None, help="Max movies to process")
    parser.add_argument("--dry-run", action="store_true", help="Count only, don't write")
    args = parser.parse_args()

    asyncio.run(backfill(limit=args.limit, dry_run=args.dry_run))
