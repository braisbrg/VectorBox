import logging
import asyncio
from typing import List, Dict, Set, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from config import AsyncSessionLocal
from models.database import UserRating, Movie
from models.schemas import FeedSection, FeedItem, FeedResponse
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.provider_service import ProviderService
from services.recommendation_service import RecommendationService
from services.recommendation_engine import RecommendationEngine

from utils.decorators import safe_execution

logger = logging.getLogger(__name__)

class FeedService:
    def __init__(self):
        self.engine = RecommendationEngine()

    @safe_execution(fallback_return=FeedSection(id="because_you_watched", title="Recommended for You", items=[]))
    async def get_because_you_watched_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, qdrant: QdrantService, seen_ids: Set[int], country: str, provider_service: ProviderService = None, background_tasks = None
    ) -> FeedSection:
        return await self.engine.get_because_you_watched_section(user_id, db, tmdb, qdrant, seen_ids, country, provider_service, background_tasks=background_tasks)

    @safe_execution(fallback_return=FeedSection(id="your_taste", title="Your Taste", items=[]))
    async def get_your_taste_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, provider_service: ProviderService = None, background_tasks = None
    ) -> FeedSection:
        return await self.engine.get_your_taste_section(user_id, db, tmdb, seen_ids, country, provider_service, background_tasks=background_tasks)

    @safe_execution(fallback_return=FeedSection(id="hidden_gems", title="Hidden Gems", items=[]))
    async def get_hidden_gems_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, provider_service: ProviderService = None, background_tasks = None
    ) -> FeedSection:
        return await self.engine.get_hidden_gems_section(user_id, db, tmdb, seen_ids, country, provider_service, background_tasks=background_tasks)

    @safe_execution(fallback_return=FeedSection(id="available_now", title="Available on Your Services", items=[]))
    async def get_available_now_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, streaming_providers: List[int]
    ) -> FeedSection:
        return await self.engine.get_available_now_section(user_id, db, tmdb, seen_ids, country, streaming_providers)

    @safe_execution(fallback_return=FeedSection(id="deep_dive", title="Deep Dive", items=[]))
    async def get_deep_dive_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, provider_service: ProviderService = None, include_low_quality: bool = False, background_tasks = None
    ) -> FeedSection:
        return await self.engine.get_deep_dive_section(user_id, db, tmdb, seen_ids, country, provider_service, include_low_quality, background_tasks=background_tasks)

    @safe_execution(fallback_return=None)
    async def get_wildcard_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, provider_service: ProviderService = None
    ) -> Optional[FeedSection]:
        return await self.engine.get_wildcard_section(user_id, db, tmdb, seen_ids, country, provider_service)

    @safe_execution(fallback_return=None)
    async def get_random_recommendations_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, provider_service: ProviderService = None
    ) -> Optional[FeedSection]:
        return await self.engine.get_random_recommendations_section(user_id, db, tmdb, seen_ids, country, provider_service)

    async def get_popular_on_letterboxd_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, country: str, provider_service: ProviderService = None
    ) -> Optional[FeedSection]:
        return await self.engine.get_popular_on_letterboxd_section(user_id, db, tmdb, country, provider_service)
        
    async def get_random_watchlist_section(self, user_id: int, db: AsyncSession, tmdb: TMDBClient, country: str, provider_service: ProviderService = None) -> Optional[FeedSection]:
        return await self.engine.get_random_watchlist_section(user_id, db, tmdb, country, provider_service)



    async def get_watchlist_feed(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        country: str,
        streaming_providers: List[int],
        background_tasks = None
    ) -> FeedResponse:
        """
        Generate a feed based ONLY on the user's watchlist.
        """
        # 1. Available Now (Watchlist)
        available_section = await self.get_available_now_section(user_id, db, tmdb, set(), country, streaming_providers)
        
        # 2. Top Rated in Watchlist
        top_rated_result = await db.execute(
            select(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                UserRating.is_watchlist.is_(True),
                UserRating.is_watched.is_(False)
            )
            .order_by(desc(Movie.vectorbox_score))
            .limit(20)
        )
        top_rated_movies = top_rated_result.scalars().all()
        
        # Batch fetch providers for top rated
        start_provider = ProviderService(db, tmdb)
        tr_ids = [m.id for m in top_rated_movies]
        tr_providers_map = await start_provider.get_providers_batch(tr_ids, country)
        
        top_rated_items = []
        for movie in top_rated_movies:
            movie_providers = tr_providers_map.get(movie.id, [])
            flat_providers = [p["provider_name"] for p in movie_providers]
            item = await self.engine.create_feed_item(movie, 1.0, country, tmdb, include_rating=True, streaming_providers=flat_providers)
            top_rated_items.append(item)
            
        top_rated_section = FeedSection(
            id="watchlist_top_rated",
            title="Top Rated in Your Watchlist",
            items=top_rated_items
        )
        
        # 3. Short & Sweet (Runtime < 100m)
        short_result = await db.execute(
            select(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                UserRating.is_watchlist.is_(True),
                UserRating.is_watched.is_(False),
                Movie.runtime < 100
            )
            .order_by(desc(Movie.vote_average))
            .limit(20)
        )
        short_movies = short_result.scalars().all()
        short_items = []
        if short_movies:
            short_ids = [m.id for m in short_movies]
            short_providers_map = await start_provider.get_providers_batch(short_ids, country)
        else:
            short_providers_map = {}
        for movie in short_movies:
            p_data = short_providers_map.get(movie.id, [])
            flat_providers = [p["provider_name"] for p in p_data]
            item = await self.engine.create_feed_item(movie, 1.0, country, tmdb, include_rating=True, streaming_providers=flat_providers)
            short_items.append(item)

            
        short_section = FeedSection(
            id="watchlist_short",
            title="Short & Sweet (Watchlist)",
            items=short_items
        )

        # 4. Random Shuffle
        local_provider_for_watchlist = ProviderService(db, tmdb)
        random_section = await self.get_random_watchlist_section(user_id, db, tmdb, country, local_provider_for_watchlist)

        
        sections = [available_section, top_rated_section, short_section, random_section]
        feed = [s for s in sections if s and s.items]
        
        return FeedResponse(feed=feed)

    async def get_hybrid_picks_section(self, user_id: int, db: AsyncSession, country: str, seen_ids: Set[int], provider_service: ProviderService = None, qdrant: QdrantService = None, background_tasks = None) -> Optional[FeedSection]:
        tmdb = provider_service.tmdb if provider_service else None
        recommender = RecommendationService(db, tmdb=tmdb, qdrant=qdrant)
        return await recommender.get_hybrid_picks_section(user_id, country, seen_ids, provider_service, background_tasks=background_tasks)

    async def get_auteur_section(self, user_id: int, db: AsyncSession, country: str, seen_ids: Set[int]) -> Optional[FeedSection]:
        # This one doesn't even take provider_service in signature here.
        # We should probably instantiate a lightweight client or rely on the default if we can't pass it.
        # But wait, we can change the signature if we update the call site.
        recommender = RecommendationService(db) 
        # We will fix the call site to pass tmdb if possible, or accept this one is less frequent (once per feed).
        return await recommender.get_auteur_section(user_id, country, seen_ids)

    async def get_main_feed(
        self,
        user_id: int,
        country_code: str,
        streaming_providers: List[int],
        tmdb: TMDBClient,
        qdrant: QdrantService,
        include_low_quality: bool = False,
        background_tasks = None
    ) -> FeedResponse:
        """
        Generate the main feed using PARALLEL EXECUTION.
        """
        
        # 1. Define Parallel Tasks with Session Isolation
        
        async def task_popular():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_popular_on_letterboxd_section(user_id, session, tmdb, country_code, local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Popular]: {e}")
                return None

        async def task_watched():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_because_you_watched_section(user_id, session, tmdb, qdrant, set(), country_code, local_provider, background_tasks=background_tasks)
            except Exception as e:
                logger.error(f"Feed Task Failed [Watched]: {e}")
                return None

        async def task_taste():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_your_taste_section(user_id, session, tmdb, set(), country_code, local_provider, background_tasks=background_tasks)
            except Exception as e:
                logger.error(f"Feed Task Failed [Taste]: {e}")
                return None

        async def task_wildcard():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_wildcard_section(user_id, session, tmdb, set(), country_code, local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Wildcard]: {e}")
                return None

        async def task_random():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_random_recommendations_section(user_id, session, tmdb, set(), country_code, local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Random]: {e}")
                return None

        async def task_hidden():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_hidden_gems_section(user_id, session, tmdb, set(), country_code, local_provider, background_tasks=background_tasks)
            except Exception as e:
                logger.error(f"Feed Task Failed [Hidden]: {e}")
                return None

        async def task_available():
            try:
                async with AsyncSessionLocal() as session:
                    return await self.get_available_now_section(user_id, session, tmdb, set(), country_code, streaming_providers)
            except Exception as e:
                logger.error(f"Feed Task Failed [Available]: {e}")
                return None

        async def task_hybrid():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    # We need to pass tmdb/qdrant to get_hybrid_picks_section
                    # But get_hybrid_picks_section signature doesn't take qdrant explicitly. 
                    # We updated RecommendationService to take them in init. 
                    # We need to update get_hybrid_picks_section to take them or handle it.
                    # The previous edit updated get_hybrid_picks_section to extract tmdb from provider_service.
                    # Qdrant is still a leak in hybrid picks if we don't pass it.
                    # Let's update get_hybrid_picks_section signature in a moment. For now, calling as is.
                    return await self.get_hybrid_picks_section(user_id, session, country_code, set(), local_provider, qdrant=qdrant, background_tasks=background_tasks)
            except Exception as e:
                logger.error(f"Feed Task Failed [Hybrid]: {e}")
                return None

        async def task_auteur():
            try:
                async with AsyncSessionLocal() as session:
                    # We should pass tmdb here too
                    recommender = RecommendationService(session, tmdb=tmdb)
                    return await recommender.get_auteur_section(user_id, country_code, set())
            except Exception as e:
                logger.error(f"Feed Task Failed [Auteur]: {e}")
                return None
        
        # 2. Execute Parallel Tasks
        tasks = [
            task_popular(),
            task_hybrid(), # Trident
            task_watched(),
            task_taste(),
            task_wildcard(),
            task_random(),
            task_hidden(),
            task_auteur(), # Signal B separate row
            task_available()
        ]
        
        results = await asyncio.gather(*tasks)
        
        # Unpack results
        section_popular, section_hybrid, section_a, section_b, section_wildcard, section_random, section_c, section_auteur, section_d = results
        
        # 3. Deduplicate and Aggregate
        seen_ids = set()
        final_sections = []
        
        ordered_results = [
            section_hybrid, 
            section_popular, 
            section_a, 
            section_b, 
            section_auteur,
            section_wildcard, 
            section_random, 
            section_c, 
            section_d
        ]
        
        for section in ordered_results:
            if not section or not section.items:
                continue
            
            unique_items = []
            for item in section.items:
                if item.id not in seen_ids:
                    unique_items.append(item)
                    seen_ids.add(item.id)
            
            if unique_items:
                section.items = unique_items
                final_sections.append(section)
        
        # 4. Deep Dive (Sequential)
        try:
            async with AsyncSessionLocal() as session:
                local_provider = ProviderService(session, tmdb)
                section_deep_dive = await self.get_deep_dive_section(
                    user_id, session, tmdb, seen_ids, country_code, local_provider, include_low_quality=include_low_quality, background_tasks=background_tasks
                )
                if section_deep_dive and section_deep_dive.items:
                    insert_pos = min(len(final_sections), 5)
                    final_sections.insert(insert_pos, section_deep_dive)
        except Exception as e:
            logger.error(f"Feed Task Failed [Deep Dive]: {e}")
        
        return FeedResponse(feed=final_sections, status="ok")
