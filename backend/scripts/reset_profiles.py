
import asyncio
import os
import sys
import logging
import argparse
from sqlalchemy import text
import redis.asyncio as redis
from openai import AsyncOpenAI
# Fix paths for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

from config import AsyncSessionLocal
from models.database import UserCluster, User, UserRating
from services.clustering_service import ClusteringService
from services.qdrant_service import QdrantService
from sqlalchemy import select

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("reset_profiles")

async def reset_profiles(force: bool = False, recluster: bool = True):
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
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    try:
        r = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        keys = await r.keys("*")
        # Broad invalidation of recommendation-related keys
        deleted = [k for k in keys if any(x in k for x in ["feed", "section", "signal", "profile", "fastapi-cache"])]
        if deleted:
            await r.delete(*deleted)
            logger.info(f"✅ Invalidated {len(deleted)} Redis cache keys (feed/section/signal/profile/fastapi-cache).")
        else:
            logger.info("ℹ️ No relevant Redis cache keys found to clear.")
        await r.close()
    except Exception as e:
        logger.error(f"❌ Failed to clear Redis: {e}")
        # Don't fail the whole script if Redis fails, DB is more important here

    logger.info("🎉 User Profile Reset Complete!")

    if recluster:
        logger.info("Starting Re-clustering for all users with ratings...")
        groq_api_key = os.getenv("GROQ_API_KEY")
        groq_client = AsyncOpenAI(
            api_key=groq_api_key,
            base_url="https://api.groq.com/openai/v1",
            max_retries=0
        ) if groq_api_key else None
    
        qdrant = QdrantService()
        clustering = ClusteringService(qdrant=qdrant)

        async with AsyncSessionLocal() as db:
            stmt = select(User.id).join(UserRating, User.id == UserRating.user_id).distinct()
            result = await db.execute(stmt)
            user_ids = result.scalars().all()

            logger.info(f"Triggering clustering for {len(user_ids)} users...")
            for uid in user_ids:
                try:
                    logger.info(f"Re-clustering User {uid}...")
                    await clustering.create_user_clusters(uid, db, groq_client=groq_client)
                    logger.info(f"✅ User {uid} re-clustered.")
                except Exception as e:
                    logger.error(f"❌ Failed for user {uid}: {e}")
    
        if groq_client:
            await groq_client.close()
    
        logger.info("🎉 Re-clustering Complete!")
    else:
        logger.info("Re-clustering skipped. Next user request will trigger fresh Cluster Generation.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    parser = argparse.ArgumentParser(description="Reset User Taste Profiles")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--no-recluster", action="store_true", help="Skip immediate re-analysis for users with ratings")
    args = parser.parse_args()
    
    asyncio.run(reset_profiles(force=args.force, recluster=not args.no_recluster))
