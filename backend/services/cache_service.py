import logging
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


async def invalidate_user_cache(user_id: int):
    """
    Invalidate all feed/signal cache keys for a user after upload or sync.
    Uses direct Redis SCAN — does not depend on fastapi-cache2.
    """
    try:
        from config import REDIS_URL
        from services.feed_service import FEED_CACHE_VERSION

        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            deleted_count = 0
            patterns = [
                f"section:{FEED_CACHE_VERSION}:{user_id}:*",
                f"signal_cache:{user_id}:*",
            ]
            for pattern in patterns:
                cursor = 0
                while True:
                    cursor, keys = await r.scan(cursor, match=pattern, count=100)
                    if keys:
                        await r.delete(*keys)
                        deleted_count += len(keys)
                    if cursor == 0:
                        break
            await r.delete(f"cluster_rotation:{FEED_CACHE_VERSION}:{user_id}")
            await r.delete(f"niche_theme_rotation:{FEED_CACHE_VERSION}:{user_id}")
            if deleted_count:
                logger.info(f"Invalidated {deleted_count} feed/signal cache keys for user_id={user_id}")
        finally:
            await r.close()

    except Exception as e:
        logger.error(f"Failed to invalidate cache for user {user_id}: {e}")
