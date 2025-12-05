import asyncio
import os
import sys
import logging
import argparse
from sqlalchemy import select, or_

# Add parent directory to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AsyncSessionLocal
from models.database import Movie
from services.omdb_client import OMDbClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("backfill_scores.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Suppress other loggers
logging.getLogger("httpx").setLevel(logging.WARNING)

async def backfill_scores(limit: int = 950):
    """
    Backfill VectorBox scores for movies that are missing them.
    Respects OMDb daily rate limits.
    """
    omdb = OMDbClient()
    if not omdb.api_key:
        logger.error("OMDb API key not found. Exiting.")
        return

    async with AsyncSessionLocal() as db:
        # Find movies with missing scores but having IMDB ID
        stmt = select(Movie).where(
            Movie.vectorbox_score == None,
            Movie.imdb_id != None
        ).limit(limit)
        
        result = await db.execute(stmt)
        movies = result.scalars().all()
        
        logger.info(f"Found {len(movies)} movies missing VectorBox scores (Limit: {limit})")
        
        updated_count = 0
        error_count = 0
        
        for movie in movies:
            try:
                logger.info(f"Fetching score for: {movie.title} ({movie.imdb_id})")
                
                omdb_data = await omdb.fetch_movie_data(movie.imdb_id)
                if omdb_data:
                    vb_data = omdb.calculate_vectorbox_score(omdb_data, movie.vote_average)
                    
                    movie.vectorbox_score = vb_data["score"]
                    movie.imdb_rating = vb_data["breakdown"]["imdb"]
                    movie.metacritic_rating = vb_data["breakdown"]["meta"]
                    movie.rotten_tomatoes_rating = vb_data["breakdown"]["rt"]
                    
                    updated_count += 1
                else:
                    logger.warning(f"No OMDb data found for {movie.title}")
                    
            except Exception as e:
                logger.error(f"Error processing {movie.title}: {e}")
                error_count += 1
                
            # Commit every 10 movies
            if updated_count % 10 == 0:
                await db.commit()
                
        await db.commit()
        logger.info(f"Backfill complete. Updated: {updated_count}, Errors: {error_count}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill VectorBox scores")
    parser.add_argument("--limit", type=int, default=950, help="Max movies to process (OMDb limit)")
    args = parser.parse_args()
    
    asyncio.run(backfill_scores(args.limit))
