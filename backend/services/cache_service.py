import logging
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


async def scan_and_delete(r, pattern: str) -> int:
    """SCAN-and-DELETE every key matching `pattern`. Returns the count.

    Per STACK_RULES.md "Redis Key Enumeration", `KEYS` is banned because
    it is O(N) and blocks the event loop. This helper wraps the SCAN
    cursor loop that every cache-invalidation site reimplemented before
    (rss.py, recommendations.py, clustering_service.py, etc.).
    """
    deleted = 0
    cursor = 0
    while True:
        cursor, keys = await r.scan(cursor, match=pattern, count=100)
        if keys:
            await r.delete(*keys)
            deleted += len(keys)
        if cursor == 0:
            break
    return deleted


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
            for pattern in (
                f"section:{FEED_CACHE_VERSION}:{user_id}:*",
                f"signal_cache:{user_id}:*",
            ):
                deleted_count += await scan_and_delete(r, pattern)
            await r.delete(f"cluster_rotation:{FEED_CACHE_VERSION}:{user_id}")
            await r.delete(f"niche_theme_rotation:{FEED_CACHE_VERSION}:{user_id}")
            if deleted_count:
                logger.info(f"Invalidated {deleted_count} feed/signal cache keys for user_id={user_id}")
        finally:
            await r.close()

    except Exception as e:
        logger.error(f"Failed to invalidate cache for user {user_id}: {e}")
