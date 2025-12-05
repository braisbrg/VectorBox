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
        self.CACHE_KEY = "trending:letterboxd:week"
        self.CACHE_TTL = 90000 # 25 hours

    async def update_letterboxd_popular(self) -> int:
        """
        Scrapes Letterboxd popular chart, resolves movies, updates ratings, and caches IDs.
        Returns number of movies found.
        """
        logger.info("Starting Letterboxd Popular Update...")
        
        # 1. Scrape
        films = self.scraper.scrape_popular_this_week()
        if not films:
            logger.warning("No popular films found during scrape.")
            return 0
            
        logger.info(f"Scraped {len(films)} films. Resolving to DB...")
        
        tmdb_ids = []
        
        for film in films:
            try:
                # 2. Resolve ID
                # Use letterboxd_slug from the scraper result
                slug = film.get("letterboxd_slug")
                if not slug:
                    continue
                    
                tmdb_id = self.scraper.get_tmdb_id(slug)
                if not tmdb_id:
                    logger.warning(f"Could not resolve TMDB ID for {slug}")
                    continue
                
                # 3. Ingest/Update
                # Construct URI
                uri = f"https://letterboxd.com/film/{slug}/"
                
                movie = await self.movie_service.get_or_create_movie(tmdb_id, letterboxd_uri=uri)
                
                if movie:
                    # Update rating if available
                    if film.get("letterboxd_rating"):
                        movie.letterboxd_rating = film["letterboxd_rating"]
                    
                    tmdb_ids.append(tmdb_id)
                    
            except Exception as e:
                logger.error(f"Error processing film {film.get('letterboxd_slug')}: {e}")
                continue
        
        # Commit DB changes (ratings)
        await self.db.commit()
        
        # 4. Cache
        if tmdb_ids:
            pipe = self.redis.pipeline()
            pipe.delete(self.CACHE_KEY)
            pipe.rpush(self.CACHE_KEY, *tmdb_ids)
            pipe.expire(self.CACHE_KEY, self.CACHE_TTL)
            pipe.execute()
            logger.info(f"Cached {len(tmdb_ids)} popular movies to Redis.")
        
        return len(tmdb_ids)

    def get_popular_movie_ids(self) -> List[int]:
        """Fetch cached popular movie IDs"""
        ids = self.redis.lrange(self.CACHE_KEY, 0, -1)
        return [int(x) for x in ids]
