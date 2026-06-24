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
        """Fetch cached popular movie IDs from Redis.

        Transparently reads both cache schemas:
          - new (Phase 8 2026-05+): list of `{tmdb_id, letterboxd_rating}` dicts
          - legacy: list of ints
        Returns just the ints for back-compat. Use `get_popular_movie_items`
        when you need the Letterboxd rating too.
        """
        items = await self.get_popular_movie_items()
        return [it["tmdb_id"] for it in items if it.get("tmdb_id") is not None]

    async def get_popular_movie_items(self) -> List[dict]:
        """Fetch cached popular movie items with `{tmdb_id, letterboxd_rating}`.

        For legacy cache values (list of ints) or the older Redis list at
        `TRENDING_WEEK_KEY`, normalizes to dicts with `letterboxd_rating=None`
        so callers can write `it["letterboxd_rating"]` unconditionally.
        """
        r = await self._get_redis()
        data = await r.get(POPULAR_IDS_KEY)

        if not data:
            legacy_ids = await r.lrange(TRENDING_WEEK_KEY, 0, -1)
            return [{"tmdb_id": int(x), "letterboxd_rating": None} for x in legacy_ids]

        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return []

        if not parsed:
            return []

        # New schema: list of dicts
        if isinstance(parsed[0], dict):
            return [p for p in parsed if isinstance(p.get("tmdb_id"), int)]

        # Legacy schema: list of ints
        return [{"tmdb_id": int(x), "letterboxd_rating": None} for x in parsed if isinstance(x, int)]

    async def close(self):
        if self._redis:
            await self._redis.close()
        await self.movie_service.close()

    async def aclose(self):
        """Canonical full-cleanup alias — matches the other service clients."""
        await self.close()
