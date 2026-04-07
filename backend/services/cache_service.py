import logging
from fastapi_cache import FastAPICache

logger = logging.getLogger(__name__)

async def invalidate_user_cache(user_id: int):
    """
    Invalidate all cache keys related to a user after upload/sync.
    Uses versioned prefix to avoid scanning unrelated keys.
    """
    try:
        redis = FastAPICache.get_backend()
        if not redis:
            logger.warning("Redis backend not available for cache invalidation")
            return

        # If using Redis directly via the backend instance
        if hasattr(redis, "redis"):
            from services.feed_service import FEED_CACHE_VERSION
            r = redis.redis
            cursor = 0
            deleted_count = 0
            while True:
                cursor, keys = await r.scan(cursor, match=f"section:{FEED_CACHE_VERSION}:{user_id}:*", count=100)
                if keys:
                    await r.delete(*keys)
                    deleted_count += len(keys)
                if cursor == 0:
                    break
            await r.delete(f"cluster_rotation:{FEED_CACHE_VERSION}:{user_id}")
            if deleted_count:
                logger.info(f"Invalidated {deleted_count} feed cache keys for user_id={user_id}")
        else:
            logger.warning("Redis backend does not expose raw redis client; cache invalidation skipped")

    except Exception as e:
        logger.error(f"Failed to invalidate cache for user {user_id}: {e}")
