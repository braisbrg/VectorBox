
import asyncio
import os
import sys
import logging
import numpy as np
from sqlalchemy import select, or_

# Fix paths for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

from config import AsyncSessionLocal
from models.database import UserRating, Movie
from services.qdrant_service import QdrantService
from services.clustering_service import ClusteringService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def debug_recommendations(user_id=1):
    logger.info(f"Debugging Recommendations for User {user_id}...")
    
    async with AsyncSessionLocal() as db:
        clustering = ClusteringService()
        qdrant = QdrantService()
        
        # 1. Calculate Centroid manually to see vector stats
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.rating.isnot(None), UserRating.is_liked.is_(True)))
        )
        all_ratings = result.all()
        logger.info(f"User has {len(all_ratings)} rated/liked movies.")
        
        vectors = []
        for rating, movie in all_ratings:
            vector = await qdrant.get_vector(movie.tmdb_id)
            if vector:
                vectors.append(vector)
            else:
                logger.warning(f"Missing vector for {movie.title} ({movie.tmdb_id})")
        
        if not vectors:
            logger.error("No vectors found!")
            return

        global_center = np.mean(vectors, axis=0).tolist()
        logger.info("Calculated Global Centroid.")
        
        # 2. Raw Search (No Threshold)
        logger.info("Performing Raw Qdrant Search (Limit 50)...")
        results = await qdrant.search_similar(
            query_vector=global_center,
            limit=50,
            score_threshold=0.0  # NO THRESHOLD
        )
        
        print("\n--- Raw Search Results (Top 10) ---")
        for i, res in enumerate(results[:10]):
            print(f"{i+1}. TMDB {res['movie_id']} - Score: {res['score']:.4f}")
            # Fetch title
            movie_res = await db.execute(select(Movie).where(Movie.tmdb_id == res['movie_id']))
            movie = movie_res.scalar_one_or_none()
            if movie:
                print(f"   Title: {movie.title}")
            else:
                print(f"   (Movie not in local DB)")

        # 3. Check Thresholds
        count_over_02 = sum(1 for r in results if r['score'] >= 0.2)
        count_over_01 = sum(1 for r in results if r['score'] >= 0.1)
        count_over_005 = sum(1 for r in results if r['score'] >= 0.05)
        
        print("\n--- Threshold Analysis ---")
        print(f"Candidates > 0.2: {count_over_02}")
        print(f"Candidates > 0.1: {count_over_01}")
        print(f"Candidates > 0.05: {count_over_005}")

        # 4. Check Watched Filtering
        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.is_watched.is_(True))
        )
        watched_ids = set(watched_result.scalars().all()) # These are INTERNAL IDs
        
        # We need TMDB IDs of watched movies to filter Qdrant results
        watched_tmdb_res = await db.execute(
             select(Movie.tmdb_id).where(Movie.id.in_(watched_ids))
        )
        watched_tmdb_ids = set(watched_tmdb_res.scalars().all())
        
        unwatched_candidates = [r for r in results if r['movie_id'] not in watched_tmdb_ids]
        print(f"\nCandidates after Watched Filter: {len(unwatched_candidates)}")
        
        # Check scores of unwatched
        unwatched_over_01 = sum(1 for r in unwatched_candidates if r['score'] >= 0.1)
        print(f"Unwatched > 0.1: {unwatched_over_01}", flush=True)

        await qdrant.client.close()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(debug_recommendations())
