"""
Fix Movies Manual
=================
Reads a CSV of manual corrections and applies fixes to the DB.

CSV format (corrections.csv):
    letterboxd_uri,correct_tmdb_id,old_tmdb_id
    https://letterboxd.com/film/black-dog-2024/,1485372,1249423
    ,1625822,1069200

Columns:
    letterboxd_uri   — canonical Letterboxd film URL (optional if old_tmdb_id provided)
    correct_tmdb_id  — the correct TMDB ID to ingest
    old_tmdb_id      — (optional) the wrong TMDB ID currently stored in DB, used as fallback lookup

Lookup order:
    1. Movie.letterboxd_uri == uri  (if uri is not empty)
    2. Movie.tmdb_id == old_tmdb_id (if step 1 fails or uri is empty)

Usage:
    docker compose exec backend python scripts/fix_movies_manual.py --dry-run
    docker compose exec backend python scripts/fix_movies_manual.py
    docker compose exec backend python scripts/fix_movies_manual.py --file /app/scripts/custom.csv
"""
import asyncio
import argparse
import csv
import os
import sys
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update, delete, func
from config import AsyncSessionLocal
from models.database import Movie, UserRating
from services.tmdb_client import TMDBClient
from services.movie_service import MovieService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corrections.csv")


async def fix_movies(csv_path: str, dry_run: bool = False) -> None:
    # --- Read CSV ---
    if not os.path.exists(csv_path):
        logger.error(f"CSV file not found: {csv_path}")
        return

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        logger.info("CSV is empty — nothing to do.")
        return

    logger.info(f"Loaded {len(rows)} corrections from {csv_path}")
    if dry_run:
        logger.info("🧪 DRY RUN — no data will be modified.")

    tmdb = TMDBClient()
    corrected = 0
    errors = 0

    try:
        for i, row in enumerate(rows, 1):
            uri = row.get("letterboxd_uri", "").strip()
            old_tmdb_id_str = row.get("old_tmdb_id", "").strip()

            try:
                correct_tmdb_id = int(row.get("correct_tmdb_id", "").strip())
            except (ValueError, AttributeError):
                logger.error(f"[{i}/{len(rows)}] Invalid correct_tmdb_id for {uri or old_tmdb_id_str} — skipping")
                errors += 1
                continue

            old_tmdb_id: int | None = None
            if old_tmdb_id_str:
                try:
                    old_tmdb_id = int(old_tmdb_id_str)
                except ValueError:
                    pass

            try:
                async with AsyncSessionLocal() as session:
                    old_movie = None
                    lookup_method = ""

                    # 1. Try lookup by letterboxd_uri
                    if uri:
                        stmt = select(Movie).where(Movie.letterboxd_uri == uri)
                        result = await session.execute(stmt)
                        old_movie = result.scalar_one_or_none()
                        if old_movie:
                            lookup_method = f"uri={uri}"

                    # 2. Fallback: lookup by old_tmdb_id
                    if not old_movie and old_tmdb_id:
                        stmt = select(Movie).where(Movie.tmdb_id == old_tmdb_id)
                        result = await session.execute(stmt)
                        old_movie = result.scalar_one_or_none()
                        if old_movie:
                            lookup_method = f"old_tmdb_id={old_tmdb_id}"

                    if not old_movie:
                        logger.warning(
                            f"[{i}/{len(rows)}] Movie not found in DB "
                            f"(uri={uri!r}, old_tmdb_id={old_tmdb_id}) — skipping"
                        )
                        continue

                    # Already correct?
                    if old_movie.tmdb_id == correct_tmdb_id:
                        logger.info(
                            f"[{i}/{len(rows)}] {old_movie.title} already has correct "
                            f"tmdb_id={correct_tmdb_id} (found via {lookup_method})"
                        )
                        continue

                    old_movie_id = old_movie.id
                    old_movie_tmdb_id = old_movie.tmdb_id

                    # Count affected ratings
                    count_stmt = select(func.count()).where(UserRating.movie_id == old_movie_id)
                    rating_count = (await session.execute(count_stmt)).scalar() or 0

                    if dry_run:
                        logger.info(
                            f"[{i}/{len(rows)}] WOULD FIX: {old_movie.title} ({old_movie.year}) "
                            f"tmdb_id {old_movie_tmdb_id} → {correct_tmdb_id}, "
                            f"{rating_count} rating(s) to migrate "
                            f"(found via {lookup_method})"
                        )
                        corrected += 1
                        continue

                    # 3. Ingest correct movie
                    movie_service = MovieService(session, tmdb)
                    new_movie = await movie_service.get_or_create_movie(
                        correct_tmdb_id,
                        letterboxd_uri=uri or old_movie.letterboxd_uri,
                    )

                    if not new_movie:
                        logger.error(f"[{i}/{len(rows)}] Failed to ingest tmdb_id={correct_tmdb_id}")
                        errors += 1
                        continue

                    # 4. Migrate UserRatings
                    if rating_count > 0:
                        migrate_stmt = (
                            update(UserRating)
                            .where(UserRating.movie_id == old_movie_id)
                            .values(movie_id=new_movie.id)
                        )
                        await session.execute(migrate_stmt)

                    # 5. Delete old movie if no ratings remain
                    remaining_stmt = select(func.count()).where(UserRating.movie_id == old_movie_id)
                    remaining = (await session.execute(remaining_stmt)).scalar() or 0

                    if remaining == 0:
                        delete_stmt = delete(Movie).where(Movie.id == old_movie_id)
                        await session.execute(delete_stmt)
                        logger.info(f"  Deleted orphan movie id={old_movie_id} (tmdb_id={old_movie_tmdb_id})")

                    await session.commit()
                    corrected += 1
                    logger.info(
                        f"[{i}/{len(rows)}] ✓ FIXED: {old_movie.title} ({old_movie.year}) "
                        f"tmdb_id {old_movie_tmdb_id} → {correct_tmdb_id}, "
                        f"{rating_count} rating(s) migrated to movie.id={new_movie.id} "
                        f"(found via {lookup_method})"
                    )

            except Exception as e:
                logger.error(f"[{i}/{len(rows)}] Error processing row: {e}")
                errors += 1

    finally:
        await tmdb.aclose()

    # --- Summary ---
    logger.info("=" * 60)
    logger.info(f"MANUAL FIX {'(DRY RUN) ' if dry_run else ''}COMPLETE")
    logger.info(f"  Total in CSV:  {len(rows)}")
    logger.info(f"  Corrected:     {corrected}")
    logger.info(f"  Errors:        {errors}")
    logger.info("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply manual TMDB ID corrections from a CSV file"
    )
    parser.add_argument(
        "--file",
        default=DEFAULT_CSV,
        help=f"Path to corrections CSV (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without modifying data",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(fix_movies(args.file, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
