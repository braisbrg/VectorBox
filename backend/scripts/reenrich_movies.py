
import asyncio
import logging
import argparse
import sys
import os

# [PATH] Ensure backend directory (/app) is in sys.path
backend_dir = "/app"
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from sqlalchemy import select
from config import AsyncSessionLocal
from models.database import Movie
from services.movie_service import MovieService
from services.tmdb_client import TMDBClient
from services.omdb_client import OMDbClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def reenrich_movies(limit: int = 100, force: bool = False):
    async with AsyncSessionLocal() as session:
        # Pass initialized clients to service (Architecture Rules)
        tmdb = TMDBClient()
        omdb = OMDbClient()
        movie_service = MovieService(session, tmdb=tmdb, omdb=omdb)

        # Find movies missing critical metadata
        # Target movies that HAVE a score but NO imdb_rating (meaning they were partially enriched)
        stmt = select(Movie)
        if not force:
            from sqlalchemy import or_
            stmt = stmt.where(or_(Movie.vectorbox_score.is_(None), Movie.imdb_rating.is_(None)))
        
        stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        movies = result.scalars().all()

        logger.info(f"Found {len(movies)} movies to re-enrich.")

        for movie in movies:
            logger.info(f"Re-enriching: {movie.title} (TMDB ID: {movie.tmdb_id})")
            # The signature is: enrich_movie(self, movie: Movie, skip_qdrant: bool = False, force: bool = False)
            success = await movie_service.enrich_movie(movie, force=True)
            if success:
                logger.info(f"Successfully enriched {movie.title}")
            else:
                logger.warning(f"Failed to enrich {movie.title}")
            
            # Small delay to avoid hitting rate limits too hard
            await asyncio.sleep(0.5)

        await session.commit()
        await tmdb.aclose()
        await omdb.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-enrich movies with missing metadata.")
    parser.add_argument("--limit", type=int, default=100, help="Number of movies to process.")
    parser.add_argument("--force", action="store_true", help="Force re-enrichment even if not missing metadata.")
    args = parser.parse_args()

    asyncio.run(reenrich_movies(limit=args.limit, force=args.force))
