import asyncio
import logging
import sys
import os

# Add backend directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from config import AsyncSessionLocal
from models.database import Movie
from services.tmdb_client import TMDBClient
from services.provider_service import ProviderService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def backfill_providers():
    """
    Iterates through all movies in the database and fetches streaming providers
    using the ProviderService (which handles caching).
    """
    tmdb = TMDBClient()
    
    async with AsyncSessionLocal() as db:
        provider_service = ProviderService(db, tmdb)
        
        # Fetch all movies
        logger.info("Fetching all movies from database...")
        result = await db.execute(select(Movie))
        movies = result.scalars().all()
        
        total_movies = len(movies)
        logger.info(f"Found {total_movies} movies to process.")
        
        processed_count = 0
        error_count = 0
        
        for movie in movies:
            try:
                processed_count += 1
                if processed_count % 10 == 0:
                    logger.info(f"Processing {processed_count}/{total_movies}...")
                
                # Fetch providers for ES (Spain) - Default for now
                # ProviderService will check cache first, then fetch from TMDB if needed
                await provider_service.get_providers(movie.id, "ES")
                
            except Exception as e:
                logger.error(f"Error processing movie {movie.title} (ID: {movie.id}): {e}")
                error_count += 1
                
        logger.info(f"Backfill complete. Processed: {processed_count}, Errors: {error_count}")

    await tmdb.close()

if __name__ == "__main__":
    asyncio.run(backfill_providers())
