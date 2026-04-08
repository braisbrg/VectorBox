import logging
import time
from redis import asyncio as aioredis

logger = logging.getLogger(__name__)

async def get_profile_summary_status(user_id: int, redis_url: str) -> tuple[bool, float]:
    """
    Check if profile is dirty and when the last summary was generated.
    Returns: (is_dirty, timestamp)
    """
    r = None
    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        is_dirty = await r.get(f"profile_dirty:{user_id}") == "true"
        timestamp = await r.get(f"profile_summary_timestamp:{user_id}")
        return is_dirty, float(timestamp) if timestamp else 0.0
    except Exception as e:
        logger.error(f"Failed to get profile summary status for user {user_id}: {e}")
        return False, 0.0
    finally:
        if r:
            await r.close()

async def set_profile_dirty(user_id: int, redis_url: str):
    """Mark the profile as dirty (needs regeneration after 10 mins)"""
    r = None
    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        await r.set(f"profile_dirty:{user_id}", "true")
        # Ensure timestamp exists if it's the first time
        exists = await r.exists(f"profile_summary_timestamp:{user_id}")
        if not exists:
             await r.set(f"profile_summary_timestamp:{user_id}", str(time.time()))
        logger.info(f"Set profile_dirty=true for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to set profile dirty for user {user_id}: {e}")
    finally:
        if r:
            await r.close()

async def get_cached_profile_summary(user_id: int, redis_url: str) -> str | None:
    """Retrieve the cached natural language summary"""
    r = None
    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        return await r.get(f"profile_summary:{user_id}")
    except Exception as e:
        logger.error(f"Failed to get cached profile summary for user {user_id}: {e}")
        return None
    finally:
        if r:
            await r.close()

async def set_cached_profile_summary(user_id: int, summary: str, redis_url: str):
    """Cache the profile summary and reset dirty flag"""
    r = None
    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        # 24h expiration for the summary itself
        await r.set(f"profile_summary:{user_id}", summary, ex=86400) 
        await r.set(f"profile_summary_timestamp:{user_id}", str(time.time()))
        await r.set(f"profile_dirty:{user_id}", "false")
        logger.info(f"Cached profile summary and reset dirty flag for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to set cached profile summary for user {user_id}: {e}")
    finally:
        if r:
            await r.close()

async def invalidate_profile_summary(user_id: int, redis_url: str):
    """Force immediate invalidation (used for RSS sync)"""
    r = None
    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        await r.delete(f"profile_summary:{user_id}")
        await r.set(f"profile_dirty:{user_id}", "true")
        # Set timestamp to 0 to force immediate regeneration on next load
        await r.set(f"profile_summary_timestamp:{user_id}", "0")
        logger.info(f"Forced profile summary invalidation for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to invalidate profile summary for user {user_id}: {e}")
    finally:
        if r:
            await r.close()
