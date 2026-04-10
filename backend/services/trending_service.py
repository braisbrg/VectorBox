import logging
import json
import os
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from services.scraper_service import ScraperService
from services.movie_service import MovieService
from config import FEED_CACHE_VERSION

logger = logging.getLogger(__name__)

POPULAR_IDS_KEY = f"cache:{FEED_CACHE_VERSION}:popular_letterboxd:ids"
TRENDING_WEEK_KEY = f"trending:{FEED_CACHE_VERSION}:letterboxd:week"

class TrendingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.scraper = ScraperService()
        self.movie_service = MovieService(db)
        self._redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self._redis: Optional[aioredis.Redis] = None

    async def _get_redis(self) -> aioredis.Redis:
        if not self._redis:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def get_popular_movie_ids(self) -> List[int]:
        """Fetch cached popular movie IDs from Redis."""
        r = await self._get_redis()
        data = await r.get(POPULAR_IDS_KEY)

        if not data:
            legacy_ids = await r.lrange(TRENDING_WEEK_KEY, 0, -1)
            return [int(x) for x in legacy_ids]

        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return []

    async def close(self):
        if self._redis:
            await self._redis.close()
        await self.movie_service.close()
