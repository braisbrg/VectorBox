
import asyncio
import os
import sys
import logging
import argparse
from sqlalchemy import text
import redis.asyncio as redis

# Fix paths for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

from config import AsyncSessionLocal
from models.database import UserCluster, User, UserRating
from services.clustering_service import ClusteringService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("reset_profiles")

async def reset_profiles(force: bool = False, reanalyze: bool = False):
    """
    Resets all computed user profile data (Clusters) and clears the application cache.
    This forces a re-analysis of all users' tastes on their next visit.
    
    Persisted Data (Safe):
    - User Accounts
    - User Ratings / Watchlist
    - Movie Metadata
    
    Deleted Data (Reset):
    - User Clusters (Taste Profiles)
    - API Cache (FastAPI Cache)
    """
    logger.info("Starting User Profile Reset...")
    
    if not force:
        print("\nWARNING: This will delete ALL calculated User Taste Clusters.")
        print("Users will need to refresh their feed to regenerate their profile.")
        print("Existing ratings and watched history will remain intact.")
        confirm = input("Are you sure? (y/N): ")
        if confirm.lower() != 'y':
            logger.info("Operation cancelled.")
            return

    # 1. Clear Database Clusters
    try:
        async with AsyncSessionLocal() as db:
            logger.info("Truncating user_clusters table...")
            # CASCADE might be risky if we add more tables linked to clusters, but for now it's fine.
            # Using DELETE ensures cascading if Foreign Keys are set up, but TRUNCATE is faster.
            # Given we want a clean slate, TRUNCATE is good.
            await db.execute(text("TRUNCATE TABLE user_clusters RESTART IDENTITY CASCADE"))
            await db.commit()
            logger.info("✅ Database User Clusters cleared.")
    except Exception as e:
        logger.error(f"❌ Database error: {e}")
        return

    # 2. Clear Redis Cache
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    try:
        r = await redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        
        # Clear FastAPI Cache (Feed responses)
        keys = await r.keys("fastapi-cache:*")
        if keys:
            await r.delete(*keys)
            logger.info(f"✅ Cleared {len(keys)} Redis cache entries (fastapi-cache).")
        else:
            logger.info("ℹ️ No Redis cache keys found to clear.")
            
        await r.close()
    except Exception as e:
        logger.error(f"❌ Failed to clear Redis: {e}")
        # Don't fail the whole script if Redis fails, DB is more important here

    logger.info("🎉 User Profile Reset Complete!")

    if reanalyze:
        logger.info("Starting Re-analysis of all user profiles...")
        clustering = ClusteringService()
        async with AsyncSessionLocal() as db:
            # Get all users who have ratings
            stmt = select(User.id).join(UserRating, User.id == UserRating.user_id).distinct()
            result = await db.execute(stmt)
            user_ids = result.scalars().all()
            
            logger.info(f"Triggering clustering for {len(user_ids)} users...")
            for uid in user_ids:
                try:
                    logger.info(f"Re-clustering User {uid}...")
                    # create_user_clusters handles its own commit/rollback
                    await clustering.create_user_clusters(uid, db)
                    logger.info(f"✅ User {uid} re-analyzed.")
                except Exception as e:
                    logger.error(f"❌ Failed to re-analyze user {uid}: {e}")
        
        logger.info("🎉 Re-analysis Complete!")
    else:
        logger.info("Next user request will trigger fresh Cluster Generation with Enriched Vectors.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    parser = argparse.ArgumentParser(description="Reset User Taste Profiles")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--reanalyze", action="store_true", help="Immediately trigger re-analysis for all users")
    args = parser.parse_args()
    
    asyncio.run(reset_profiles(force=args.force, reanalyze=args.reanalyze))
