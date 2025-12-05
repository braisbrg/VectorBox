import asyncio
import logging
import os
import sys

# Add parent directory to path to allow imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, or_
from config import AsyncSessionLocal
from models.database import Movie, MovieAvailability
from services.omdb_client import OMDbClient
from services.tmdb_client import TMDBClient
from services.provider_service import ProviderService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def backfill_metadata(limit: int = 50):
    """
    Backfill VectorBox Scores, Spanish metadata, and Streaming Providers for existing movies.
    """
    omdb = OMDbClient()
    tmdb = TMDBClient()
    
    if not omdb.api_key:
        logger.error("OMDB_API_KEY not found! Cannot calculate scores.")
        return

    async with AsyncSessionLocal() as db:
        provider_service = ProviderService(db, tmdb)
        
        # Find movies with missing scores OR missing provider data OR missing translations
        stmt = (
            select(Movie)
            .outerjoin(MovieAvailability, (Movie.id == MovieAvailability.movie_id) & (MovieAvailability.country_code == 'ES'))
            .where(
                or_(
                    Movie.vectorbox_score == None,
                    MovieAvailability.id == None,
                    Movie.title_es == None
                )
            )
            .limit(limit)
        )
        
        result = await db.execute(stmt)
        movies = result.scalars().all()
        
        logger.info(f"Found {len(movies)} movies to update (Limit: {limit})...")
        
        if not movies:
            return

        # 1. Batch Update Providers
        logger.info("Fetching streaming providers...")
        movie_ids = [m.id for m in movies]
        await provider_service.get_providers_batch(movie_ids, "ES")
        logger.info("Providers updated.")

        updated_count = 0
        
        for movie in movies:
            try:
                # Check if we need to process metadata (Score or Translation)
                needs_score = movie.vectorbox_score is None
                needs_translation = movie.title_es is None
                
                if not needs_score and not needs_translation:
                    continue

                logger.info(f"Processing Metadata: {movie.title} ({movie.year})")
                
                # 1. Fetch TMDB Details if we need IMDb ID OR Spanish Metadata
                if not movie.imdb_id or needs_translation:
                    # Force refresh if we specifically need translation (to bypass stale cache without es data)
                    details = await tmdb.get_movie_details(movie.tmdb_id, force_refresh=needs_translation)
                    if details:
                        if not movie.imdb_id and details.get("imdb_id"):
                            movie.imdb_id = details.get("imdb_id")
                        
                        if needs_translation:
                            movie.title_es = details.get("title_es")
                            movie.overview_es = details.get("overview_es")
                            if movie.title_es:
                                logger.info(f"  -> Fetched Spanish translation: {movie.title_es}")
                
                # 2. Fetch OMDb Data (Only if we need score)
                if needs_score:
                    if movie.imdb_id:
                        omdb_data = await omdb.fetch_movie_data(movie.imdb_id)
                        if omdb_data:
                            # 3. Calculate Score
                            vb_data = omdb.calculate_vectorbox_score(omdb_data, movie.vote_average)
                            
                            # 4. Update Movie
                            movie.imdb_rating = vb_data["breakdown"]["imdb"]
                            movie.metacritic_rating = vb_data["breakdown"]["meta"]
                            movie.rotten_tomatoes_rating = vb_data["breakdown"]["rt"]
                            movie.vectorbox_score = vb_data["score"]
                            
                            logger.info(f"  -> Updated! Score: {movie.vectorbox_score}")
                        else:
                            logger.warning(f"  -> No OMDb data found for {movie.imdb_id}")
                    else:
                        logger.warning(f"  -> No IMDb ID found for {movie.title}")
                
                updated_count += 1
                    
            except Exception as e:
                logger.error(f"Error processing {movie.title}: {e}")
                
        await db.commit()
        logger.info(f"Backfill complete! Updated metadata for {updated_count} movies.")
        # Note: Providers are committed inside provider_service

if __name__ == "__main__":
    # Run with a limit (default 50) to avoid hitting API limits too hard
    # Usage: python scripts/backfill_metadata.py [limit]
    limit = 50
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except ValueError:
            pass
            
    asyncio.run(backfill_metadata(limit))
