"""
Reconcile Letterboxd Movies
============================
Verifies that the TMDB IDs stored in the DB for movies with a letterboxd_uri
actually match what TMDB's search API returns for the same title+year.

Supports an exclusions file (reconcile_exclusions.txt) to skip movies that
have been manually verified as correct despite TMDB search disagreement.

Usage:
    docker compose exec backend python scripts/reconcile_letterboxd_movies.py          # report only
    docker compose exec backend python scripts/reconcile_letterboxd_movies.py --fix    # fix mismatches
    docker compose exec backend python scripts/reconcile_letterboxd_movies.py --add-exclusion 1249423
"""
import asyncio
import argparse
import os
import sys
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update
from config import AsyncSessionLocal
from models.database import Movie, UserRating
from services.tmdb_client import TMDBClient
from services.movie_service import MovieService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

EXCLUSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reconcile_exclusions.txt")


def load_exclusions() -> set[int]:
    """Load excluded TMDB IDs from the exclusions file. Ignores missing file and # comments."""
    exclusions: set[int] = set()
    if not os.path.exists(EXCLUSIONS_FILE):
        return exclusions
    with open(EXCLUSIONS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                exclusions.add(int(line))
            except ValueError:
                logger.warning(f"Invalid exclusion entry (not an integer): {line!r}")
    logger.info(f"Loaded {len(exclusions)} exclusions from {EXCLUSIONS_FILE}")
    return exclusions


def add_exclusion(tmdb_id: int) -> None:
    """Append a TMDB ID to the exclusions file."""
    # Check if already present
    existing = load_exclusions()
    if tmdb_id in existing:
        logger.info(f"tmdb_id={tmdb_id} is already in exclusions file.")
        return

    with open(EXCLUSIONS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{tmdb_id}\n")
    logger.info(f"Added tmdb_id={tmdb_id} to {EXCLUSIONS_FILE}")


async def reconcile(fix: bool = False) -> None:
    exclusions = load_exclusions()
    tmdb = TMDBClient()
    skipped_excluded = 0

    try:
        async with AsyncSessionLocal() as session:
            # 1. Fetch all movies with a letterboxd_uri
            stmt = select(Movie).where(Movie.letterboxd_uri.isnot(None))
            result = await session.execute(stmt)
            movies = result.scalars().all()

            total = len(movies)
            mismatches: list[dict] = []

            logger.info(f"Found {total} movies with letterboxd_uri — starting reconciliation…")

            for i, movie in enumerate(movies, 1):
                # Skip excluded movies
                if movie.tmdb_id in exclusions:
                    skipped_excluded += 1
                    continue

                slug = movie.letterboxd_uri.rstrip("/").split("/")[-1]

                search_result = await tmdb.search_movie(movie.title, movie.year)

                if not search_result:
                    logger.debug(f"[{i}/{total}] No TMDB result for '{movie.title}' ({movie.year}) — skipping")
                    continue

                result_id = search_result["id"]

                if result_id != movie.tmdb_id:
                    # Check year tolerance (≤1 year difference)
                    result_year = None
                    release_date = search_result.get("release_date", "")
                    if release_date and len(release_date) >= 4:
                        try:
                            result_year = int(release_date[:4])
                        except ValueError:
                            pass

                    year_diff = abs(movie.year - result_year) if (movie.year and result_year) else 0

                    if year_diff <= 1:
                        logger.warning(
                            f"MISMATCH: {movie.title} ({movie.year}) "
                            f"— stored tmdb_id={movie.tmdb_id}, TMDB returned={result_id}"
                        )
                        mismatches.append({
                            "movie_id": movie.id,
                            "title": movie.title,
                            "year": movie.year,
                            "slug": slug,
                            "stored_tmdb_id": movie.tmdb_id,
                            "correct_tmdb_id": result_id,
                            "letterboxd_uri": movie.letterboxd_uri,
                        })

                if i % 50 == 0:
                    logger.info(f"Progress: {i}/{total} processed, {len(mismatches)} mismatches so far")

            # --- Summary ---
            logger.info("=" * 60)
            logger.info(f"RECONCILIATION COMPLETE")
            logger.info(f"  Total processed:  {total}")
            logger.info(f"  Total mismatches: {len(mismatches)}")
            if skipped_excluded:
                logger.info(f"  Skipped {skipped_excluded} excluded movies (verified correct)")
            logger.info("=" * 60)

            if not mismatches:
                logger.info("✅ No mismatches found — database is consistent.")
                return

            for m in mismatches:
                logger.info(
                    f"  • {m['title']} ({m['year']}) "
                    f"stored={m['stored_tmdb_id']} → correct={m['correct_tmdb_id']}"
                )

            # --- Fix mode ---
            if not fix:
                logger.info("Run with --fix to correct these mismatches.")
                return

            logger.info("Applying fixes…")
            fixed = 0

            for m in mismatches:
                try:
                    async with AsyncSessionLocal() as fix_session:
                        movie_service = MovieService(fix_session, tmdb)

                        # Re-ingest with the correct TMDB ID
                        new_movie = await movie_service.get_or_create_movie(
                            m["correct_tmdb_id"],
                            letterboxd_uri=m["letterboxd_uri"],
                        )

                        if not new_movie:
                            logger.error(f"  ✗ Failed to ingest correct movie for '{m['title']}'")
                            continue

                        # Update all UserRatings pointing to the old movie
                        update_stmt = (
                            update(UserRating)
                            .where(UserRating.movie_id == m["movie_id"])
                            .values(movie_id=new_movie.id)
                        )
                        result = await fix_session.execute(update_stmt)
                        await fix_session.commit()

                        rows_updated = result.rowcount
                        fixed += 1
                        logger.info(
                            f"  ✓ {m['title']}: "
                            f"tmdb_id {m['stored_tmdb_id']}→{m['correct_tmdb_id']}, "
                            f"{rows_updated} rating(s) migrated to movie.id={new_movie.id}"
                        )

                except Exception as e:
                    logger.error(f"  ✗ Error fixing '{m['title']}': {e}")

            logger.info(f"Fixed {fixed}/{len(mismatches)} mismatches.")

    finally:
        await tmdb.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile Letterboxd movie TMDB IDs"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Apply corrections (re-ingest + migrate UserRatings). Without this flag, only reports.",
    )
    parser.add_argument(
        "--add-exclusion",
        type=int,
        metavar="TMDB_ID",
        help="Append a TMDB ID to the exclusions file and exit. No reconciliation is run.",
    )
    args = parser.parse_args()

    if args.add_exclusion:
        add_exclusion(args.add_exclusion)
        return

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(reconcile(fix=args.fix))


if __name__ == "__main__":
    main()
