import asyncio
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, or_

from config import AsyncSessionLocal
from models.database import Movie
from services.tmdb_client import TMDBClient
from services.omdb_client import OMDbClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)

REFRESH_STRATEGIES = [
    ("recent",  365,       7,  "< 1 year old → refresh weekly"),
    ("mid",     365 * 5,   30, "1-5 years → refresh monthly"),
    ("classic", None,      90, "5+ years → refresh quarterly"),
]


async def get_movies_to_refresh(db, strategy: str, limit: int) -> list:
    now = datetime.utcnow()

    if strategy == "recent":
        cutoff_year = now.year - 1
        threshold = now - timedelta(days=7)
        query = select(Movie).where(
            Movie.year >= cutoff_year,
            or_(
                Movie.last_metadata_refresh.is_(None),
                Movie.last_metadata_refresh < threshold,
            ),
        )
    elif strategy == "mid":
        year_min = now.year - 5
        year_max = now.year - 1
        threshold = now - timedelta(days=30)
        query = select(Movie).where(
            Movie.year >= year_min,
            Movie.year < year_max,
            or_(
                Movie.last_metadata_refresh.is_(None),
                Movie.last_metadata_refresh < threshold,
            ),
        )
    else:  # classic
        cutoff_year = now.year - 5
        threshold = now - timedelta(days=90)
        query = select(Movie).where(
            Movie.year < cutoff_year,
            or_(
                Movie.last_metadata_refresh.is_(None),
                Movie.last_metadata_refresh < threshold,
            ),
        )

    result = await db.execute(query.limit(limit))
    return result.scalars().all()


async def refresh_movie(movie: Movie, tmdb: TMDBClient, omdb: OMDbClient) -> bool:
    try:
        tmdb_data = await tmdb.get_movie_details(movie.tmdb_id, force_refresh=True)
        if not tmdb_data:
            return False

        movie.vote_count = tmdb_data.get("vote_count", movie.vote_count)
        movie.vote_average = tmdb_data.get("vote_average", movie.vote_average)
        movie.popularity = tmdb_data.get("popularity", movie.popularity)
        movie.poster_path = tmdb_data.get("poster_path", movie.poster_path)
        genres = tmdb_data.get("genres")
        if genres:
            movie.genres = [g["name"] for g in genres]
        movie.runtime = tmdb_data.get("runtime", movie.runtime)

        if movie.imdb_id and movie.vote_count and movie.vote_count >= 10:
            omdb_data = await omdb.fetch_movie_data(movie.imdb_id)
            vb = omdb.calculate_vectorbox_score(omdb_data, movie.vote_average, movie.vote_count)
            if vb.score is not None:
                movie.vectorbox_score = min(vb.score, 98)

        movie.last_metadata_refresh = datetime.utcnow()
        return True

    except Exception as e:
        logger.warning(f"Failed to refresh movie {movie.id} ({movie.title}): {e}")
        return False


async def run(strategy: str, limit: int, dry_run: bool) -> None:
    strategies = ["recent", "mid", "classic"] if strategy == "all" else [strategy]

    tmdb = TMDBClient()
    omdb = OMDbClient()

    try:
        for s in strategies:
            logger.info(f"Strategy: {s} | limit: {limit} | dry_run: {dry_run}")
            async with AsyncSessionLocal() as db:
                movies = await get_movies_to_refresh(db, s, limit)
                logger.info(f"[{s}] Found {len(movies)} movies due for refresh")

                if dry_run:
                    for m in movies:
                        logger.info(f"  DRY-RUN: would refresh {m.id} {m.title} ({m.year})")
                    continue

                updated = 0
                for movie in movies:
                    ok = await refresh_movie(movie, tmdb, omdb)
                    if ok:
                        updated += 1

                await db.commit()
                logger.info(f"[{s}] Refreshed {updated}/{len(movies)} movies")
    finally:
        await tmdb.aclose()
        await omdb.close()


def main():
    parser = argparse.ArgumentParser(description="Refresh movie metadata from TMDB/OMDb")
    parser.add_argument(
        "--strategy",
        choices=["recent", "mid", "classic", "all"],
        default="recent",
        help="Which age cohort to refresh",
    )
    parser.add_argument("--limit", type=int, default=100, help="Max movies to refresh per run")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be refreshed without updating")
    args = parser.parse_args()

    asyncio.run(run(args.strategy, args.limit, args.dry_run))


if __name__ == "__main__":
    main()
