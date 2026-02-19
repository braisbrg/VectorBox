
import asyncio
import os
import sys
import logging
from typing import List, Dict

# Fix paths for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
# If running from backend/ add current_dir to path, if running from root add backend/
sys.path.append(current_dir)

from config import AsyncSessionLocal
from models.database import Movie
from services.clustering_service import ClusteringService
from models.schemas import RecommendationRequest
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def debug_grid_view(user_id=1):
    # Override QDRANT_URL for local debugging (Host -> Container)
    os.environ["QDRANT_URL"] = "http://localhost:6333"
    
    # Init Cache
    FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")
    
    logger.info(f"Debugging Grid View (General Recs) for User {user_id}...")
    
    async with AsyncSessionLocal() as db:
        clustering = ClusteringService()
        
        # Simulate Request: Limit 20, Page 1
        # Similar to frontend
        filters = {
             # Add strictly for testing if needed, or empty for global
             # "streaming_providers": [8], # Netflix (check DB for ID)
             # "country_code": "ES"
        }
        
        logger.info("Fetching Page 1...")
        results = await clustering.get_item_based_recommendations(
            user_id=user_id,
            db=db,
            filters=filters,
            limit=20,
            page=1
        )
        
        print(f"\n--- Page 1 Results ({len(results)}) ---")
        for i, res in enumerate(results):
             title = res.get("title", "Unknown")
             score = res.get("score", 0)
             # Contributors usually tell us WHY it was recommended
             contributors = res.get("contributors", [])
             top_contr = contributors[0] if contributors else "None"
             print(f"{i+1}. {title} (Score: {score:.2f}) - Because: {top_contr}")
             
        # Check specific movie "Lemony Snicket" if present?
        
        logger.info("Fetching Page 2...")
        results_p2 = await clustering.get_item_based_recommendations(
            user_id=user_id,
            db=db,
            filters=filters,
            limit=20,
            page=2
        )
        print(f"\n--- Page 2 Results ({len(results_p2)}) ---")
        for i, res in enumerate(results_p2):
             title = res.get("title", "Unknown")
             print(f"{i+1}. {title}")

        await clustering.qdrant.client.close()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(debug_grid_view())
