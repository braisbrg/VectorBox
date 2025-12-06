
import asyncio
import os
import sys
import logging
from sqlalchemy import text
import redis.asyncio as redis

# Fix paths for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

from config import AsyncSessionLocal
from models.database import UserCluster

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def reset_profiles():
    logger.info("Starting User Profile Reset...")
    
    # 1. Clear Database Clusters
    async with AsyncSessionLocal() as db:
        logger.info("Truncating user_clusters table...")
        await db.execute(text("TRUNCATE TABLE user_clusters RESTART IDENTITY CASCADE"))
        await db.commit()
        logger.info("Database User Clusters cleared.")

    # 2. Clear Redis Cache
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    try:
        r = await redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        
        # Pattern for FastAPI cache
        keys = await r.keys("fastapi-cache:*")
        if keys:
            await r.delete(*keys)
            logger.info(f"Cleared {len(keys)} Redis cache entries.")
        else:
            logger.info("No Redis cache keys found to clear.")
            
        await r.close()
    except Exception as e:
        logger.error(f"Failed to clear Redis: {e}")

    logger.info("User Profile Reset Complete. Next user request will regenerate fresh clusters with Enriched Vectors.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(reset_profiles())
