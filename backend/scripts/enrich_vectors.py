
import asyncio
import os
import sys
import logging
from sqlalchemy import select, func
from sentence_transformers import SentenceTransformer

# Fix paths for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

from config import AsyncSessionLocal
from models.database import Movie
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def enrich_vectors():
    logger.info("Starting Keyword Enrichment & Re-Vectorization...")
    
    tmdb = TMDBClient()
    qdrant = QdrantService()
    embedding_service = EmbeddingService()
    
    # Initialize Qdrant if needed
    try:
        await qdrant.init_collection()
    except:
        pass

    async with AsyncSessionLocal() as db:
        # Fetch all movies
        # In production, use cursor/pagination. For <10k movies, fetching all ID/Title/Overview/Keywords is safeish.
        # Let's fetch IDs first to be safe memory-wise
        result = await db.execute(select(Movie.id).order_by(Movie.id))
        movie_ids = result.scalars().all()
        
        logger.info(f"Found {len(movie_ids)} movies to process.")
        
        processed_count = 0
        updated_keywords_count = 0
        
        # Batch size for Qdrant upserts
        BATCH_SIZE = 50
        batch_points = []
        
        for mid in movie_ids:
            # Fetch full movie object
            stmt = select(Movie).where(Movie.id == mid)
            res = await db.execute(stmt)
            movie = res.scalar_one_or_none()
            
            if not movie:
                continue
            
            # 1. Enrich Keywords if missing
            if not movie.keywords:
                logger.info(f"Fetching keywords for {movie.title} ({movie.tmdb_id})...")
                keywords = await tmdb.get_movie_keywords(movie.tmdb_id)
                if keywords:
                    movie.keywords = keywords
                    updated_keywords_count += 1
                    # Commit keyword changes incrementally
                    await db.commit() 
                else:
                    movie.keywords = [] # Mark as empty list to avoid re-fetching nulls indefinitely?
            
            # 2. Generate New Vector (New Format: "Themes: ...")
            try:
                vector = embedding_service.generate_embedding({
                    "title": movie.title,
                    "overview": movie.overview,
                    "genres": movie.genres,
                    "keywords": movie.keywords or []
                })
                
                # 3. Prepare Qdrant Point
                # We reuse existing metadata logic but ensuring keywords are included
                metadata = {
                    "title": movie.title,
                    "year": movie.year,
                    "genres": movie.genres,
                    "rating": movie.vote_average,
                    "vote_count": movie.vote_count,
                    "runtime": movie.runtime,
                    "poster_path": movie.poster_path,
                    "vectorbox_score": movie.vectorbox_score,
                    "imdb_rating": movie.imdb_rating,
                    "metacritic_rating": movie.metacritic_rating,
                    "rotten_tomatoes_rating": movie.rotten_tomatoes_rating,
                    "title_es": movie.title_es,
                    "overview_es": movie.overview_es,
                    "keywords": movie.keywords
                }
                
                # We can't batch easily with current QdrantService wrapper (upsert takes 1).
                # But QdrantService.upsert_movie_vector is async, so we can await it.
                # For speed, we could modify wrapper, but calling it is safer.
                await qdrant.upsert_movie_vector(
                    movie_id=movie.tmdb_id,
                    vector=vector.tolist(),
                    metadata=metadata
                )
                
            except Exception as e:
                logger.error(f"Failed to process {movie.title}: {e}")
            
            processed_count += 1
            if processed_count % 10 == 0:
                logger.info(f"Processed {processed_count}/{len(movie_ids)} movies.")
        
        logger.info("Enrichment Complete!")
        logger.info(f"Total Movies: {processed_count}")
        logger.info(f"Keywords Fetched: {updated_keywords_count}")
        
    await tmdb.close()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(enrich_vectors())
