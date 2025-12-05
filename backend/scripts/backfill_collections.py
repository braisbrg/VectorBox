import asyncio
import logging
import os
import sys

# Add parent directory to path to allow imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from config import AsyncSessionLocal
from models.database import Movie
from services.tmdb_client import TMDBClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def backfill_collections(batch_size: int = 50):
    """
    Backfill collection_id for ALL existing movies that don't have one.
    """
    tmdb = TMDBClient()
    
    async with AsyncSessionLocal() as db:
        # 1. Get ALL IDs where collection_id is NULL
        logger.info("Fetching all movie IDs with missing collection_id...")
        stmt = select(Movie.id, Movie.tmdb_id, Movie.title).where(Movie.collection_id == None)
        result = await db.execute(stmt)
        movies_to_check = result.all()
        
        total_movies = len(movies_to_check)
        logger.info(f"Found {total_movies} movies to check.")
        
        if total_movies == 0:
            return

        updated_count = 0
        processed_count = 0
        
        # 2. Process in batches
        for i in range(0, total_movies, batch_size):
            batch = movies_to_check[i : i + batch_size]
            
            logger.info(f"Processing batch {i//batch_size + 1} ({len(batch)} movies)...")
            
            for movie_row in batch:
                processed_count += 1
                m_id, tmdb_id, title = movie_row
                
                try:
                    # Fetch TMDB Details
                    details = await tmdb.get_movie_details(tmdb_id)
                    
                    if details:
                        collection_info = details.get("belongs_to_collection")
                        if collection_info:
                            collection_id = collection_info.get("id")
                            if collection_id:
                                # Update DB
                                # We need to fetch the object to update it attached to session, 
                                # or use an update statement. Update statement is faster.
                                from sqlalchemy import update
                                await db.execute(
                                    update(Movie)
                                    .where(Movie.id == m_id)
                                    .values(collection_id=collection_id)
                                )
                                updated_count += 1
                                logger.info(f"[{processed_count}/{total_movies}] Found Collection for '{title}': ID {collection_id}")
                            else:
                                 # logger.info(f"[{processed_count}/{total_movies}] No Collection for '{title}'")
                                 pass
                        else:
                            # logger.info(f"[{processed_count}/{total_movies}] No Collection for '{title}'")
                            pass
                    else:
                        logger.warning(f"Could not fetch details for {title} (ID: {tmdb_id})")

                except Exception as e:
                    logger.error(f"Error processing {title}: {e}")
            
            # Commit after each batch to save progress
            await db.commit()
            
    logger.info(f"Backfill complete! Checked {total_movies} movies. Updated {updated_count} with collections.")
    await tmdb.close()

if __name__ == "__main__":
    # Usage: python scripts/backfill_collections.py [batch_size]
    batch_size = 50
    if len(sys.argv) > 1:
        try:
            batch_size = int(sys.argv[1])
        except ValueError:
            pass
            
    asyncio.run(backfill_collections(batch_size))
