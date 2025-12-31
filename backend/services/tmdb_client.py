"""
TMDB API Client with Redis caching and rate limiting
Security: API key protection, rate limiting, input sanitization
"""
import httpx
import redis.asyncio as redis
import json
import asyncio
import os
from typing import Optional, Dict, List
from datetime import timedelta
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class TMDBClient:
    """TMDB API wrapper with intelligent caching and rate limiting"""
    
    BASE_URL = "https://api.themoviedb.org/3"
    RATE_LIMIT_DELAY = 0.25  # 4 requests/second (40 per 10s with buffer)
    
    def __init__(self):
        self.api_key = os.getenv("TMDB_API_KEY")
        self.read_token = os.getenv("TMDB_READ_TOKEN")
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis_client: Optional[redis.Redis] = None
        self.last_request_time = 0
        
        # Security: Validate API credentials exist
        if not self.api_key or not self.read_token:
            raise ValueError("TMDB API credentials not configured")
    
    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection"""
        if not self.redis_client:
            self.redis_client = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
        return self.redis_client
    
    async def _rate_limit(self):
        """Enforce rate limiting to respect TMDB API limits"""
        current_time = asyncio.get_event_loop().time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.RATE_LIMIT_DELAY:
            await asyncio.sleep(self.RATE_LIMIT_DELAY - time_since_last)
        
        self.last_request_time = asyncio.get_event_loop().time()
    
    async def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make HTTP request with error handling and rate limiting"""
        await self._rate_limit()
        
        url = f"{self.BASE_URL}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.read_token}",
            "Content-Type": "application/json"
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"TMDB API error {e.response.status_code}: {endpoint}")
            if e.response.status_code == 429:  # Rate limited
                logger.warning("TMDB rate limit hit, backing off...")
                await asyncio.sleep(2)
            return None
        except Exception as e:
            logger.error(f"TMDB request failed: {e}")
            return None
    
    async def search_movie(self, title: str, year: Optional[int] = None) -> Optional[Dict]:
        """
        Search for a movie by title and optional year
        Security: Input sanitization via URL encoding
        """
        # Security: Sanitize title (httpx handles URL encoding)
        title = title.strip()[:500]  # Limit length
        
        cache_key = f"tmdb:search:{title}:{year or 'any'}"
        r = await self._get_redis()
        
        # Check cache first (7 days TTL)
        cached = await r.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for movie search: {title}")
            return json.loads(cached)
        
        # Make API request
        params = {"query": title, "include_adult": "false"}
        if year:
            params["year"] = year
        
        data = await self._make_request("/search/movie", params)
        
        if data and data.get("results"):
            result = data["results"][0]  # Best match
            await r.setex(cache_key, timedelta(days=7), json.dumps(result))
            return result
        
        return None
    
    async def get_movie_details(self, tmdb_id: int, force_refresh: bool = False) -> Optional[Dict]:
        """
        Get detailed movie information
        Cache: 7 days
        """
        cache_key = f"tmdb:movie:{tmdb_id}"
        r = await self._get_redis()
        
        if not force_refresh:
            cached = await r.get(cache_key)
            if cached:
                logger.debug(f"Cache hit for movie {tmdb_id}")
                return json.loads(cached)
        
        # Phase 13: Data Enrichment - Fetch keywords and credits in single call
        params = {"append_to_response": "keywords,credits"}
        data = await self._make_request(f"/movie/{tmdb_id}", params=params)
        
        if data:
            # Process keywords into flat list
            if "keywords" in data and "keywords" in data["keywords"]:
                data["keywords_flat"] = [k["name"] for k in data["keywords"]["keywords"]]
            else:
                data["keywords_flat"] = []
                
            # Process Directors from credits
            data["directors"] = []
            if "credits" in data and "crew" in data["credits"]:
                data["directors"] = [
                    member["name"] 
                    for member in data["credits"]["crew"] 
                    if member.get("job") == "Director"
                ]
            
            # Process Cast from credits (Top 3)
            data["cast"] = []
            if "credits" in data and "cast" in data["credits"]:
                sorted_cast = sorted(data["credits"]["cast"], key=lambda x: x.get("order", 999))
                data["cast"] = [member["name"] for member in sorted_cast[:3]]

            # Phase 12: Fetch Spanish metadata
            try:
                es_data = await self._make_request(f"/movie/{tmdb_id}", params={"language": "es-ES"})
                if es_data:
                    title_es = es_data.get("title")
                    overview_es = es_data.get("overview")
                    
                    # Only add if different from English and not empty
                    if title_es and title_es != data.get("title"):
                        data["title_es"] = title_es
                    if overview_es and overview_es != data.get("overview"):
                        data["overview_es"] = overview_es
            except Exception as e:
                logger.warning(f"Failed to fetch Spanish metadata for {tmdb_id}: {e}")

            await r.setex(cache_key, timedelta(days=7), json.dumps(data))
        
        return data
    
    async def get_movie_recommendations(self, tmdb_id: int, page: int = 1) -> List[Dict]:
        """
        Get similar movies based on TMDB's collaborative filtering algorithm.
        This provides high-quality "More Like This" recommendations.
        """
        cache_key = f"tmdb:recommendations:{tmdb_id}:{page}"
        r = await self._get_redis()
        
        cached = await r.get(cache_key)
        if cached:
            return json.loads(cached)
            
        data = await self._make_request(f"/movie/{tmdb_id}/recommendations", {"page": page})
        
        if data and data.get("results"):
            results = data["results"]
            await r.setex(cache_key, timedelta(days=3), json.dumps(results))
            return results
            
        return []
    
    async def get_movie_watch_providers(self, tmdb_id: int, country_code: str = "ES") -> Optional[Dict]:
        """
        Get streaming availability (JustWatch data via TMDB)
        Cache: 24 hours (availability changes frequently)
        Security: Validate country code format
        """
        # Security: Validate country code (2-letter ISO)
        if not country_code or len(country_code) != 2 or not country_code.isalpha():
            country_code = "ES"
        country_code = country_code.upper()
        
        cache_key = f"tmdb:providers:{tmdb_id}:{country_code}"
        r = await self._get_redis()
        
        cached = await r.get(cache_key)
        if cached:
            return json.loads(cached)
        
        data = await self._make_request(f"/movie/{tmdb_id}/watch/providers")
        
        if data and data.get("results"):
            # Extract country-specific providers
            country_data = data["results"].get(country_code, {})
            await r.setex(cache_key, timedelta(hours=24), json.dumps(country_data))
            return country_data
        
        return None

    async def get_watch_providers(self, tmdb_id: int, country: str = "ES") -> Optional[Dict]:
        """
        Get streaming availability (JustWatch data via TMDB)
        Cache: 24 hours (availability changes frequently)
        Security: Validate country code format
        """
        # Security: Validate country code (2-letter ISO)
        if not country or len(country) != 2 or not country.isalpha():
            country = "ES"
        country = country.upper()
        
        cache_key = f"tmdb:providers:{tmdb_id}:{country}"
        r = await self._get_redis()
        
        cached = await r.get(cache_key)
        if cached:
            return json.loads(cached)
        
        data = await self._make_request(f"/movie/{tmdb_id}/watch/providers")
        
        if data and data.get("results"):
            # Extract country-specific providers
            country_data = data["results"].get(country, {})
            await r.setex(cache_key, timedelta(hours=24), json.dumps(country_data))
            return country_data
        
        return None
    
    async def get_movie_keywords(self, tmdb_id: int) -> List[str]:
        """Get movie keywords for enhanced embeddings"""
        cache_key = f"tmdb:keywords:{tmdb_id}"
        r = await self._get_redis()
        
        cached = await r.get(cache_key)
        if cached:
            return json.loads(cached)
        
        data = await self._make_request(f"/movie/{tmdb_id}/keywords")
        
        keywords = []
        if data and data.get("keywords"):
            keywords = [kw["name"] for kw in data["keywords"][:10]]  # Limit to 10
            await r.setex(cache_key, timedelta(days=7), json.dumps(keywords))
        
        return keywords
    
    async def discover_movies(
        self,
        with_genres: Optional[List[int]] = None,
        without_genres: Optional[List[int]] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        vote_average_min: Optional[float] = None,
        vote_average_max: Optional[float] = None,
        vote_count_min: Optional[int] = 100,  # Filter out obscure movies
        with_runtime_min: Optional[int] = None,
        with_runtime_max: Optional[int] = None,
        sort_by: str = "vote_average.desc",
        page: int = 1
    ) -> List[Dict]:
        """
        Discover movies using TMDB's Discover API.
        Returns movies from the global TMDB database based on filters.
        """
        params = {
            "api_key": self.api_key,
            "language": "en-US",
            "sort_by": sort_by,
            "page": page,
            "vote_count.gte": vote_count_min,  # Only movies with enough votes
        }
        
        if with_genres:
            params["with_genres"] = ",".join(map(str, with_genres))
        if without_genres:
            params["without_genres"] = ",".join(map(str, without_genres))
        if year_min:
            params["primary_release_date.gte"] = f"{year_min}-01-01"
        if year_max:
            params["primary_release_date.lte"] = f"{year_max}-12-31"
        if vote_average_min:
            params["vote_average.gte"] = vote_average_min
        if vote_average_max:
            params["vote_average.lte"] = vote_average_max
        if with_runtime_min:
            params["with_runtime.gte"] = with_runtime_min
        if with_runtime_max:
            params["with_runtime.lte"] = with_runtime_max
        
        try:
            await self._rate_limit()
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/discover/movie",
                    params=params
                )
                response.raise_for_status()
                data = response.json()
                return data.get("results", [])
        except Exception as e:
            logger.error(f"TMDB discover failed: {e}")
            return []
    
    async def close(self):
        """Close Redis connection"""
        if self.redis_client:
            await self.redis_client.close()
            self.redis_client = None
            logger.info("TMDB client closed")
