import logging
import asyncio
import os
import redis.asyncio as aioredis
from typing import List, Dict, Set, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from config import AsyncSessionLocal, REDIS_URL
from models.database import UserRating, Movie
from models.schemas import FeedSection, FeedItem, FeedResponse
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.provider_service import ProviderService
from services.recommendation_service import RecommendationService
from services.recommendation_engine import RecommendationEngine
from services.embedding_service import EmbeddingService

from utils.decorators import safe_execution

logger = logging.getLogger(__name__)

# Bump when FeedItem/FeedSection schema changes to auto-invalidate Redis cache
FEED_CACHE_VERSION = "v2"

class FeedService:
    def __init__(self, qdrant: QdrantService = None, embedding_service: EmbeddingService = None):
        self.engine = RecommendationEngine(qdrant=qdrant, embedding_service=embedding_service)

    @safe_execution(fallback_return=FeedSection(id="because_you_watched", title="Recommended for You", items=[]))
    async def get_because_you_watched_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, qdrant: QdrantService, seen_ids: Set[int], country: str, provider_service: ProviderService = None, background_tasks = None
    ) -> FeedSection:
        return await self.engine.get_because_you_watched_section(user_id, db, tmdb, qdrant, seen_ids, country, provider_service, background_tasks=background_tasks)

    @safe_execution(fallback_return=FeedSection(id="your_taste", title="Your Taste", items=[]))
    async def get_your_taste_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, provider_service: ProviderService = None, background_tasks = None, groq_client = None
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
        available_section = await self.get_available_now_section(user_id, db, tmdb, set(), country, streaming_providers)
        
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
        
        start_provider = ProviderService(db, tmdb)
        tr_ids =[m.id for m in top_rated_movies]
        tr_providers_map = await start_provider.get_providers_batch(tr_ids, country)
        
        top_rated_items =[]
        for movie in top_rated_movies:
            movie_providers = tr_providers_map.get(movie.id,[])
            flat_providers = [p["provider_name"] for p in movie_providers]
            item = await self.engine.create_feed_item(movie, 1.0, country, tmdb, include_rating=True, streaming_providers=flat_providers)
            top_rated_items.append(item)
            
        top_rated_section = FeedSection(
            id="watchlist_top_rated",
            title="Top Rated in Your Watchlist",
            items=top_rated_items
        )
        
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
            short_ids =[m.id for m in short_movies]
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

        local_provider_for_watchlist = ProviderService(db, tmdb)
        random_section = await self.get_random_watchlist_section(user_id, db, tmdb, country, local_provider_for_watchlist)

        sections = [available_section, top_rated_section, short_section, random_section]
        feed = [s for s in sections if s and s.items]
        
        return FeedResponse(feed=feed)

    async def get_hybrid_picks_section(self, user_id: int, db: AsyncSession, country: str, seen_ids: Set[int], provider_service: ProviderService = None, qdrant: QdrantService = None, background_tasks = None, redis_client = None) -> Optional[FeedSection]:
        tmdb = provider_service.tmdb if provider_service else None
        recommender = RecommendationService(db, tmdb=tmdb, qdrant=qdrant, redis_client=redis_client)
        return await recommender.get_hybrid_picks_section(user_id, country, seen_ids, provider_service, background_tasks=background_tasks)

    async def get_auteur_section(self, user_id: int, db: AsyncSession, country: str, seen_ids: Set[int], tmdb: TMDBClient = None, qdrant: QdrantService = None, provider_service: ProviderService = None, redis_client = None) -> Optional[FeedSection]:
        recommender = RecommendationService(db, tmdb=tmdb, qdrant=qdrant, redis_client=redis_client)
        return await recommender.get_auteur_section(user_id, country, seen_ids, provider_service=provider_service)

    async def get_cult_actor_section(self, user_id: int, db: AsyncSession, country: str, seen_ids: Set[int], tmdb: TMDBClient = None, qdrant: QdrantService = None, provider_service: ProviderService = None, redis_client = None) -> Optional[FeedSection]:
        recommender = RecommendationService(db, tmdb=tmdb, qdrant=qdrant, redis_client=redis_client)
        return await recommender.get_cult_actor_section(user_id, country, seen_ids, provider_service=provider_service)

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
        Generate the main feed using FULLY PARALLEL EXECUTION.
        Includes high-level Redis caching for blazing fast loads.
        """
        # --- CACHE INTERCEPT BLOCK ---
        redis_url = REDIS_URL
        r = None
        try:
            r = await aioredis.from_url(redis_url, decode_responses=True)
            prov_str = ",".join(map(str, sorted(streaming_providers)))
            cache_key = f"feed:{FEED_CACHE_VERSION}:{user_id}:{country_code}:global:{include_low_quality}:{prov_str}"
            
            cached = await r.get(cache_key)
            if cached:
                logger.info(f"Feed Cache HIT for User {user_id} (0.05s response)")
                await r.close()
                return FeedResponse.model_validate_json(cached)
        except Exception as e:
            logger.warning(f"Redis feed cache read failed: {e}")
        # --- END CACHE INTERCEPT ---

        # --- PRE-POPULATE watched tmdb_ids so every signal excludes them ---
        watched_tmdb_ids: Set[int] = set()
        try:
            async with AsyncSessionLocal() as session:
                watched_result = await session.execute(
                    select(UserRating.movie_id)
                    .where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
                )
                watched_internal_ids = set(watched_result.scalars().all())

                if watched_internal_ids:
                    movies_result = await session.execute(
                        select(Movie.tmdb_id).where(Movie.id.in_(watched_internal_ids))
                    )
                    watched_tmdb_ids = set(movies_result.scalars().all())

            logger.info(f"Pre-populated {len(watched_tmdb_ids)} watched tmdb_ids for User {user_id}")
        except Exception as e:
            logger.error(f"Failed to pre-populate watched_tmdb_ids: {e}")
        # --- END PRE-POPULATE ---

        # --- GROQ CLIENT INJECTION (Phase 12: Profile Summary) ---
        groq_api_key = os.getenv("GROQ_API_KEY")
        groq_client = None
        if groq_api_key:
            try:
                from openai import AsyncOpenAI
                groq_client = AsyncOpenAI(api_key=groq_api_key, base_url="https://api.groq.com/openai/v1")
            except ImportError:
                logger.warning("openai package not found, Groq features disabled")
        # --- END GROQ CLIENT ---

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
                    return await self.get_because_you_watched_section(user_id, session, tmdb, qdrant, watched_tmdb_ids.copy(), country_code, local_provider, background_tasks=background_tasks)
            except Exception as e:
                logger.error(f"Feed Task Failed [Watched]: {e}")
                return None

        async def task_taste():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_your_taste_section(
                        user_id, session, tmdb, watched_tmdb_ids.copy(), 
                        country_code, local_provider, background_tasks=background_tasks
                    )
            except Exception as e:
                logger.error(f"Feed Task Failed [Taste]: {e}")
                return None

        async def task_wildcard():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_wildcard_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Wildcard]: {e}")
                return None

        async def task_random():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_random_recommendations_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Random]: {e}")
                return None

        async def task_hidden():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_hidden_gems_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, local_provider, background_tasks=background_tasks)
            except Exception as e:
                logger.error(f"Feed Task Failed [Hidden]: {e}")
                return None

        async def task_available():
            try:
                async with AsyncSessionLocal() as session:
                    return await self.get_available_now_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, streaming_providers)
            except Exception as e:
                logger.error(f"Feed Task Failed [Available]: {e}")
                return None

        async def task_hybrid():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_hybrid_picks_section(user_id, session, country_code, watched_tmdb_ids.copy(), local_provider, qdrant=qdrant, background_tasks=background_tasks, redis_client=r)
            except Exception as e:
                logger.error(f"Feed Task Failed [Hybrid]: {e}")
                return None

        async def task_auteur():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    recommender = RecommendationService(session, tmdb=tmdb, qdrant=qdrant, redis_client=r)
                    return await recommender.get_auteur_section(user_id, country_code, watched_tmdb_ids.copy(), provider_service=local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Auteur]: {e}")
                return None

        async def task_cult_actor():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    recommender = RecommendationService(session, tmdb=tmdb, qdrant=qdrant, redis_client=r)
                    return await recommender.get_cult_actor_section(user_id, country_code, watched_tmdb_ids.copy(), provider_service=local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Cult Actor]: {e}")
                return None

        async def task_deep_dive():
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_deep_dive_section(
                        user_id, session, tmdb, watched_tmdb_ids.copy(), country_code,
                        local_provider, include_low_quality=include_low_quality,
                        background_tasks=background_tasks
                    )
            except Exception as e:
                logger.error(f"Feed Task Failed [Deep Dive]: {e}")
                return None

        tasks =[
            task_popular(),
            task_hybrid(),
            task_watched(),
            task_taste(),
            task_wildcard(),
            task_random(),
            task_hidden(),
            task_auteur(),
            task_cult_actor(),
            task_available(),
            task_deep_dive(),
        ]
        
        results = []
        try:
            results = await asyncio.gather(*tasks)
        finally:
            if groq_client:
                await groq_client.close()
                logger.info("Groq client closed after feed generation")

        (
            section_popular,
            section_hybrid,
            section_a,
            section_b,
            section_wildcard,
            section_random,
            section_c,
            section_auteur,
            section_cult_actor,
            section_d,
            section_deep_dive,
        ) = results

        # Deduplicate and assemble in display order
        seen_ids: Set[int] = set()
        final_sections = []

        ordered_results =[
            section_hybrid,
            section_popular,
            section_a,
            section_b,
            section_auteur,
            section_cult_actor,
            section_wildcard,
            section_random,
            section_c,
            section_d,
        ]

        for section in ordered_results:
            if not section or not section.items:
                continue
            unique_items =[]
            for item in section.items:
                if item.id not in seen_ids:
                    unique_items.append(item)
                    seen_ids.add(item.id)
            if unique_items:
                section.items = unique_items
                final_sections.append(section)

        if section_deep_dive and section_deep_dive.items:
            unique_items =[item for item in section_deep_dive.items if item.id not in seen_ids]
            for item in unique_items:
                seen_ids.add(item.id)
            if unique_items:
                section_deep_dive.items = unique_items
                insert_pos = min(len(final_sections), 5)
                final_sections.insert(insert_pos, section_deep_dive)

        final_resp = FeedResponse(feed=final_sections, status="ok")

        # --- CACHE SAVE BLOCK ---
        if r:
            try:
                # Defense: only cache if feed is "complete" (>= 3 sections)
                # to avoid poisoning cache during cold starts/warmups.
                if len(final_sections) >= 3:
                    await r.setex(cache_key, 3600, final_resp.model_dump_json())
                    logger.info(f"Feed Cache MISS. Computed and saved for User {user_id}")
                else:
                    logger.warning(
                        f"Feed too thin ({len(final_sections)} sections) for User {user_id}. SKIPPING CACHE."
                    )
            except Exception as e:
                logger.warning(f"Redis feed cache write failed: {e}")
            finally:
                await r.close()
        # --- END CACHE SAVE ---

        return final_resp