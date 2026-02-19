import asyncio
import os
import sys
import logging
import argparse
from sqlalchemy import select, func

# Fix paths
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AsyncSessionLocal
from models.database import Movie
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService
from tqdm import tqdm

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

async def enrich_vectors(missing_only: bool = True, limit: int = None):
    """
    Refetches keywords for movies and regenerates their vectors.
    """
    logger.info("Starting Keyword Enrichment & Vector Regeneration...")
    
    tmdb = TMDBClient()
    qdrant = QdrantService()
    embedding_service = EmbeddingService()
    
    # Init Qdrant just in case
    await qdrant.init_collection()

    async with AsyncSessionLocal() as db:
        # 1. Select Candidates
        query = select(Movie)
        
        if missing_only:
            logger.info("Targeting movies with EMPTY metadata (Keywords/Directors/Cast)...")
        else:
            logger.info("Targeting ALL movies (Force Refresh)...")

        result = await db.execute(query)
        all_movies = result.scalars().all()
        
        # Filter in memory
        candidates = []
        for m in all_movies:
            if missing_only:
                # Check keywords, directors, OR cast
                if not m.keywords or not m.directors or not m.cast:
                    candidates.append(m)
            else:
                candidates.append(m)
        
        if limit:
            candidates = candidates[:limit]

        logger.info(f"files to process: {len(candidates)}")

        if not candidates:
            return

        # 2. Process
        pbar = tqdm(total=len(candidates), desc="Enriching Metadata")
        
        success_count = 0
        
        for movie in candidates:
            try:
                # Flag to check if we need to update DB
                db_updated = False
                
                # Check what is missing
                needs_keywords = not movie.keywords or not missing_only
                needs_credits = not movie.directors or not movie.cast or not missing_only
                
                fetched_details = None
                
                # Fetch fresh details if needed
                if needs_keywords or needs_credits:
                    fetched_details = await tmdb.get_movie_details(movie.tmdb_id)
                    
                if fetched_details:
                    # Update Keywords
                    if needs_keywords:
                        movie.keywords = fetched_details.get("keywords_flat", [])
                        db_updated = True
                        
                    # Update Credits (Directors & Cast)
                    if needs_credits:
                        # Directors handled in get_movie_details -> "directors" key
                        movie.directors = fetched_details.get("directors", [])
                        
                        # Cast - Extract Top 3
                        cast_list = []
                        if "credits" in fetched_details and "cast" in fetched_details["credits"]:
                            # Sort by order just in case, though TMDB usually returns sorted
                            sorted_cast = sorted(fetched_details["credits"]["cast"], key=lambda x: x.get("order", 999))
                            cast_list = [member["name"] for member in sorted_cast[:3]]
                        
                        movie.cast = cast_list
                        db_updated = True

                    # Update Spanish Metadata (Self-Healing)
                    if not movie.title_es or not movie.overview_es:
                         if fetched_details.get("title_es"): 
                             movie.title_es = fetched_details.get("title_es")
                             db_updated = True
                         if fetched_details.get("overview_es"): 
                             movie.overview_es = fetched_details.get("overview_es")
                             db_updated = True
                
                if db_updated:
                    db.add(movie)

                # B. Generate NEW Embedding
                # We need genres as well
                genres = movie.genres or []
                overview = movie.overview or ""
                title = movie.title or ""
                keywords = movie.keywords or []
                
                # Optional: Include Directors/Cast in embedding text?
                # For now, sticking to standard v1 embedding logic to maintain consistency
                
                embedding_data = {
                    "title": title,
                    "overview": overview,
                    "genres": genres,
                    "keywords": keywords 
                }
                
                vector = embedding_service.generate_embedding(embedding_data)
                
                # C. Upsert to Qdrant
                payload = {
                    "tmdb_id": movie.tmdb_id,
                    "title": title,
                    "year": movie.year,
                    "genres": genres,
                    "overview": overview,
                    "poster_path": movie.poster_path,
                    "vote_average": movie.vote_average,
                    "vote_count": movie.vote_count,
                    "runtime": movie.runtime,
                    "original_language": movie.original_language,
                    "keywords": keywords,
                    "directors": movie.directors, # Add to payload
                    "cast": movie.cast,           # Add to payload
                    "vectorbox_score": movie.vectorbox_score,
                    "imdb_rating": movie.imdb_rating,
                    "metacritic_rating": movie.metacritic_rating,
                    "rotten_tomatoes_rating": movie.rotten_tomatoes_rating,
                    "title_es": movie.title_es,
                    "overview_es": movie.overview_es
                }
                
                await qdrant.upsert_movie_vector(
                    movie_id=movie.tmdb_id, # Use TMDB ID for consistency with seed_db and ingest
                    vector=vector.tolist(),
                    metadata=payload
                )
                
                success_count += 1
                
                # Commit every 50
                if success_count % 50 == 0:
                    await db.commit()
                    
            except Exception as e:
                logger.error(f"Failed to enrich movie {movie.title} ({movie.id}): {e}")
            
            pbar.update(1)
            
        await db.commit() # Final commit
        pbar.close()
        
    await tmdb.aclose()
    logger.info(f"Enrichment Complete. Updated {success_count} movies.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Process ALL movies, not just those missing keywords")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of movies processed")
    args = parser.parse_args()
    
    asyncio.run(enrich_vectors(missing_only=not args.all, limit=args.limit))
