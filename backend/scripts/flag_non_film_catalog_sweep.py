"""Sweep the existing catalog with `is_likely_non_film` and flag matches.

One-shot helper for the first deploy after the heuristic ships. Re-running
it later is safe (idempotent — only flips False -> True). New ingests get
flagged at source by `MovieFactory.build_movie`, so this script's job ends
after the initial catch-up.

Usage:
    docker compose exec -e PYTHONPATH=/app backend python -m scripts.flag_non_film_catalog_sweep [--dry-run]
"""
import argparse
import asyncio
import logging

from sqlalchemy import select, update

from config import AsyncSessionLocal
from models.database import Movie
from services.movie_factory import is_likely_non_film

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main(dry_run: bool):
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(
                Movie.id, Movie.tmdb_id, Movie.title, Movie.year,
                Movie.genres, Movie.directors, Movie.runtime, Movie.overview,
                Movie.is_excluded,
            )
        )).all()

        to_flag = []
        already_flagged = 0
        for m in rows:
            should_flag = is_likely_non_film(
                title=m.title, year=m.year, runtime=m.runtime,
                genres=m.genres, directors=m.directors, overview=m.overview,
            )
            if should_flag:
                if m.is_excluded:
                    already_flagged += 1
                else:
                    to_flag.append(m)

        logger.info(f"Total films scanned:    {len(rows)}")
        logger.info(f"Already flagged (skip): {already_flagged}")
        logger.info(f"Would flag now:         {len(to_flag)}")

        for m in to_flag:
            logger.info(
                f"  tmdb={m.tmdb_id:>8}  yr={m.year}  runtime={m.runtime}  "
                f"dirs={len(m.directors or [])}  ovr={len(m.overview or '')}  "
                f"genres={m.genres}  '{(m.title or '')[:60]}'"
            )

        if dry_run:
            logger.info("DRY-RUN — no DB writes.")
            return

        if not to_flag:
            logger.info("Nothing to do.")
            return

        ids = [m.id for m in to_flag]
        await db.execute(
            update(Movie).where(Movie.id.in_(ids)).values(is_excluded=True)
        )
        await db.commit()
        logger.info(f"Flagged {len(ids)} films as is_excluded=True.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
