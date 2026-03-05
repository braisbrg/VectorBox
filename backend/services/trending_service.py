import logging
import json
import os
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from services.scraper_service import ScraperService
from services.movie_service import MovieService

logger = logging.getLogger(__name__)

class TrendingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.scraper = ScraperService()
        self.movie_service = MovieService(db)
        self._redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self._redis: Optional[aioredis.Redis] = None

    async def _get_redis(self) -> aioredis.Redis:
        if not self._redis:
            self._redis = await aioredis.from_url(
                self._redis_url, decode_responses=True
            )
        return self._redis

    async def update_letterboxd_popular(self) -> int:
        """Legacy method. Use popular_scraper.py instead."""
        logger.warning("Using legacy internal scraper. Prefer running popular_scraper.py")
        return 0

    async def get_popular_movie_ids(self) -> List[int]:
        """Fetch cached popular movie IDs from Redis."""
        r = await self._get_redis()
        key = "cache:feed:popular_letterboxd:ids"
        data = await r.get(key)

        if not data:
            legacy_ids = await r.lrange("trending:letterboxd:week", 0, -1)
            return [int(x) for x in legacy_ids]

        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return []
