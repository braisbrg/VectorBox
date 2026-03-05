import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from sqlalchemy.dialects.postgresql import insert

from models.database import MovieAvailability, Movie
from services.tmdb_client import TMDBClient

logger = logging.getLogger(__name__)

class ProviderService:
    """
    Service to handle persistent caching of streaming providers.
    Replaces/Augments Redis caching with PostgreSQL storage for efficient joins.
    """
    
    CACHE_DURATION_DAYS = 7
    
    def __init__(self, db: AsyncSession, tmdb: TMDBClient):
        self.db = db
        self.tmdb = tmdb
        
    async def get_providers(self, movie_id: int, country_code: str = "ES") -> List[Dict]:
        """
        Get providers for a single movie.
        Returns list of dicts: [{"provider_id": 123, "provider_name": "Netflix"}, ...]
        """
        # 1. Check DB
        stmt = select(MovieAvailability).where(
            MovieAvailability.movie_id == movie_id,
            MovieAvailability.country_code == country_code
        )
        result = await self.db.execute(stmt)
        availability = result.scalar_one_or_none()
        
        if availability:
            # Check freshness
            if availability.last_updated > datetime.utcnow() - timedelta(days=self.CACHE_DURATION_DAYS):
                return availability.providers or []
            else:
                logger.info(f"Provider cache stale for movie {movie_id}, refreshing...")
        
        # 2. Fetch from TMDB
        movie_stmt = select(Movie).where(Movie.id == movie_id)
        movie_res = await self.db.execute(movie_stmt)
        movie = movie_res.scalar_one_or_none()
        
        # Release implicit read transaction locks before HTTP call
        await self.db.commit()
        
        if not movie:
            logger.warning(f"Movie {movie_id} not found in DB")
            return []
            
        providers_data = await self.tmdb.get_watch_providers(movie.tmdb_id, country_code)
        
        # Extract flatrate providers
        providers_list = []
        if providers_data:
            for provider_type in ["flatrate", "free"]:
                if provider_type in providers_data:
                    for p in providers_data[provider_type]:
                        providers_list.append({
                            "provider_id": p["provider_id"],
                            "provider_name": p["provider_name"]
                        })
        
        # Deduplicate by ID
        unique_providers = {p["provider_id"]: p for p in providers_list}.values()
        final_providers = list(unique_providers)
        
        # 3. Update DB
        if availability:
            availability.providers = final_providers
            availability.last_updated = datetime.utcnow()
        else:
            new_availability = MovieAvailability(
                movie_id=movie_id,
                country_code=country_code,
                providers=final_providers,
                last_updated=datetime.utcnow()
            )
            self.db.add(new_availability)
            
        try:
            await self.db.commit()
        except Exception as e:
            await self.db.rollback()
            logger.error(f"DB commit failed in get_providers: {e}")
            raise
        
        return final_providers

    async def get_providers_batch(self, movie_ids: List[int], country_code: str = "ES") -> Dict[int, List[Dict]]:
        """
        Efficiently get providers for multiple movies.
        Returns a map of movie_id -> list of provider dicts.
        """
        if not movie_ids:
            return {}
            
        # 1. Check DB for all movies
        stmt = select(MovieAvailability).where(
            MovieAvailability.movie_id.in_(movie_ids),
            MovieAvailability.country_code == country_code
        )
        result = await self.db.execute(stmt)
        availabilities = result.scalars().all()
        
        availability_map = {a.movie_id: a for a in availabilities}
        
        # Identify missing or stale movies
        missing_ids = []
        final_results = {}
        
        for mid in movie_ids:
            avail = availability_map.get(mid)
            if avail and avail.last_updated > datetime.utcnow() - timedelta(days=self.CACHE_DURATION_DAYS):
                final_results[mid] = avail.providers or []
            else:
                missing_ids.append(mid)
                
        if not missing_ids:
            return final_results
            
        # 2. Fetch missing from TMDB
        movie_stmt = select(Movie).where(Movie.id.in_(missing_ids))
        movie_res = await self.db.execute(movie_stmt)
        movies = movie_res.scalars().all()
        
        # Release implicit read transaction locks before HTTP batch call
        await self.db.commit()
        
        # Concurrent Fetching
        tasks = [self.tmdb.get_watch_providers(movie.tmdb_id, country_code) for movie in movies]
        # Return exceptions=True to prevent one failure from crashing the batch
        tmdb_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, movie in enumerate(movies):
            providers_data = tmdb_results[i]
            
            if isinstance(providers_data, Exception) or not providers_data:
                if isinstance(providers_data, Exception):
                     logger.error(f"Failed to fetch providers for movie {movie.id}: {providers_data}")
                final_providers = []
                # Don't cache empty failures? Or cache empty? 
                # If network error, maybe don't cache. If valid empty, cache.
                # TMDBClient returns None on error.
                if providers_data is None: 
                    continue
            else:
                 providers_list = []
                 for provider_type in ["flatrate", "free"]:
                     if provider_type in providers_data:
                         for p in providers_data[provider_type]:
                             providers_list.append({
                                 "provider_id": p["provider_id"],
                                 "provider_name": p["provider_name"]
                             })
                 
                 unique_providers = {p["provider_id"]: p for p in providers_list}.values()
                 final_providers = list(unique_providers)
            
            final_results[movie.id] = final_providers
            
            # Update DB
            avail = availability_map.get(movie.id)
            if avail:
                avail.providers = final_providers
                avail.last_updated = datetime.utcnow()
            else:
                new_avail = MovieAvailability(
                    movie_id=movie.id,
                    country_code=country_code,
                    providers=final_providers,
                    last_updated=datetime.utcnow()
                )
                self.db.add(new_avail)
        
        try:
            await self.db.commit()
        except Exception as e:
            await self.db.rollback()
            logger.error(f"DB commit failed in get_providers_batch: {e}")
            raise
        
        return final_results

    async def save_providers(self, movie_id: int, country_code: str, providers_raw: List[Dict]):
        """
        Save providers data directly (without fetching).
        Used during ingestion when data is pre-fetched via super-request.
        """
        # Deduplicate and Format
        providers_list = []
        for p in providers_raw:
             # JustWatch raw format usually has provider_id, provider_name
             providers_list.append({
                "provider_id": p["provider_id"],
                "provider_name": p["provider_name"]
            })
            
        unique_providers = {p["provider_id"]: p for p in providers_list}.values()
        final_providers = list(unique_providers)

        # Upsert into DB
        stmt = insert(MovieAvailability).values(
            movie_id=movie_id,
            country_code=country_code,
            providers=final_providers,
            last_updated=datetime.utcnow()
        ).on_conflict_do_update(
            index_elements=["movie_id", "country_code"],
            set_={"providers": final_providers, "last_updated": datetime.utcnow()}
        )
        try:
            await self.db.execute(stmt)
        except Exception as e:
            logger.error(f"Failed to save providers via upsert: {e}")
            raise
