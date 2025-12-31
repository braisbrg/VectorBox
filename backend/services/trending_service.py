import logging
import json
import redis
import os
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from services.scraper_service import ScraperService
from services.movie_service import MovieService

logger = logging.getLogger(__name__)

class TrendingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.scraper = ScraperService()
        self.movie_service = MovieService(db)
        self.redis = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
    async def update_letterboxd_popular(self) -> int:
        """
        Legacy method. Use popular_scraper.py instead.
        """
        logger.warning("Using legacy internal scraper. Prefer running popular_scraper.py")
        return 0

    def get_popular_movie_ids(self) -> List[int]:
        """Fetch cached popular movie IDs"""
        # Read from the key used by popular_scraper.py
        key = "cache:feed:popular_letterboxd:ids"
        data = self.redis.get(key)
        
        if not data:
            # Fallback to legacy key just in case
            legacy_ids = self.redis.lrange("trending:letterboxd:week", 0, -1)
            return [int(x) for x in legacy_ids]
            
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return []
