import logging
from fastapi_cache import FastAPICache

logger = logging.getLogger(__name__)

async def invalidate_user_cache(user_id: int):
    """
    Invalidate all cache keys related to a user.
    This includes feed, recommendations, and other user-specific data.
    """
    try:
        redis = FastAPICache.get_backend()
        if not redis:
            logger.warning("Redis backend not available for cache invalidation")
            return

        # Pattern matching for user-specific keys
        # We assume keys are constructed like "feed:{user_id}:..." or "recommendations:{user_id}:..."
        # The user requested clearing "cache:recommendations:{user_id}:*"
        # FastAPICache usually prefixes keys. We need to be careful.
        # If using RedisBackend, we can use scan_iter or keys (careful with keys in prod)
        
        # We will use a broad pattern to ensure we catch everything
        pattern = f"*{user_id}*" 
        
        # Ideally, we should use specific prefixes if we know them.
        # Based on the user request: "cache:recommendations:{user_id}:*"
        
        # Let's try to clear specific patterns we know we will use
        patterns = [
            f"feed:{user_id}*",
            f"recommendations:{user_id}*",
            f"watchlist:{user_id}*",
            f"clusters:{user_id}*"
        ]
        
        for p in patterns:
            await redis.clear(namespace=None, key=p) # This might not work as expected depending on backend implementation
            
        # If using Redis directly via the backend instance
        if hasattr(redis, "redis"):
            r = redis.redis
            # Scan for keys matching the user ID
            # This is expensive but necessary if keys are not well-namespaced
            async for key in r.scan_iter(match=f"*{user_id}*"):
                await r.delete(key)
                
        logger.info(f"Invalidated cache for user {user_id}")
        
    except Exception as e:
        logger.error(f"Failed to invalidate cache for user {user_id}: {e}")
