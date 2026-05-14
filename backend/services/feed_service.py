import logging
import asyncio
import redis.asyncio as aioredis
from typing import List, Dict, Set, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from config import AsyncSessionLocal, REDIS_URL, FEED_CACHE_VERSION
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

SECTION_CACHE_TTLS: dict[str, int] = {
    "popular_letterboxd":  86400,  # 24h — changes once daily via scraper
    "available_now":       3600,   # 1h — provider availability
    "because_you_watched": 7200,   # 2h
    "niche_picks":         7200,   # 2h
    "hidden_gems":         7200,   # 2h
    "picked_for_you":      7200,   # 2h
    "wildcard":            3600,   # 1h — some randomness desired
    "random_picks":        0,      # never cache — random by design
    "cult_actor":          7200,   # 2h
    "auteur":              7200,   # 2h
    "upcoming":            86400,  # 24h — changes daily with seed/refresh runs
}
DEFAULT_SECTION_TTL = 3600

async def _get_cached_section(
    r, user_id: int, section_id: str, country_code: str, prov_str: str
) -> FeedSection | None:
    if not r:
        return None
    ttl = SECTION_CACHE_TTLS.get(section_id, DEFAULT_SECTION_TTL)
    if ttl == 0:
        return None  # never cache
    key = f"section:{FEED_CACHE_VERSION}:{user_id}:{section_id}:{country_code}:{prov_str}"
    try:
        cached = await r.get(key)
        if cached:
            return FeedSection.model_validate_json(cached)
    except Exception:
        pass
    return None

async def _cache_section(
    r, user_id: int, section: FeedSection, country_code: str, prov_str: str
) -> None:
    if not r:
        return
    ttl = SECTION_CACHE_TTLS.get(section.id, DEFAULT_SECTION_TTL)
    if ttl == 0:
        return
    key = f"section:{FEED_CACHE_VERSION}:{user_id}:{section.id}:{country_code}:{prov_str}"
    try:
        await r.setex(key, ttl, section.model_dump_json())
    except Exception:
        pass

class FeedService:
    def __init__(self, qdrant: QdrantService = None, embedding_service: EmbeddingService = None):
        self.engine = RecommendationEngine(qdrant=qdrant, embedding_service=embedding_service)

    @safe_execution(fallback_return=FeedSection(id="because_you_watched", title="Recommended for You", items=[]))
    async def get_because_you_watched_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, qdrant: QdrantService, seen_ids: Set[int], country: str, provider_service: ProviderService = None, background_tasks = None, precomputed_anti_vector = None
    ) -> FeedSection:
        return await self.engine.get_because_you_watched_section(user_id, db, tmdb, qdrant, seen_ids, country, provider_service, background_tasks=background_tasks, precomputed_anti_vector=precomputed_anti_vector)

    @safe_execution(fallback_return=FeedSection(id="niche_picks", title="Niche Picks", items=[]))
    async def get_niche_picks_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, provider_service: ProviderService = None, background_tasks = None
    ) -> FeedSection:
        return await self.engine.get_niche_picks_section(user_id, db, tmdb, seen_ids, country, provider_service, background_tasks=background_tasks)

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

    async def get_main_feed(
        self,
        user_id: int,
        country_code: str,
        streaming_providers: List[int],
        tmdb: TMDBClient,
        qdrant: QdrantService,
        background_tasks = None,
        redis_client = None,
    ) -> FeedResponse:
        """
        Generate the main feed using FULLY PARALLEL EXECUTION.
        Includes high-level Redis caching for blazing fast loads.
        """
        # --- CACHE INTERCEPT BLOCK ---
        # Prefer the injected lifespan singleton (shared connection pool).
        # Only create a new client if nothing was injected (e.g. direct test calls).
        # r_is_local tracks ownership: we must NOT close the injected singleton or
        # subsequent requests will fail with "connection closed".
        r = redis_client
        r_is_local = False
        prov_str = ",".join(map(str, sorted(streaming_providers)))
        if r is None:
            try:
                r = aioredis.from_url(REDIS_URL, decode_responses=True)
                r_is_local = True
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}")
                r = None
        # --- END CACHE INTERCEPT ---

        # --- FIX 4: Pre-compute anti_vector once — Signal A and Signal B both need it ---
        precomputed_anti_vector = None
        try:
            async with AsyncSessionLocal() as session:
                precomputed_anti_vector = await self.engine._get_anti_vector(user_id, session, qdrant)
            if precomputed_anti_vector:
                logger.info(f"Pre-computed anti_vector for user_id={user_id} ({len(precomputed_anti_vector)} dims)")
        except Exception as e:
            logger.warning(f"Anti-vector pre-compute failed for user_id={user_id}: {e}")
        # --- END FIX 4 ---

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

        async def task_popular():
            cached = await _get_cached_section(r, user_id, "popular_letterboxd", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_popular_on_letterboxd_section(user_id, session, tmdb, country_code, local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Popular]: {e}")
                return None

        async def task_watched():
            cached = await _get_cached_section(r, user_id, "because_you_watched", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_because_you_watched_section(user_id, session, tmdb, qdrant, watched_tmdb_ids.copy(), country_code, local_provider, background_tasks=background_tasks, precomputed_anti_vector=precomputed_anti_vector)
            except Exception as e:
                logger.error(f"Feed Task Failed [Watched]: {e}")
                return None

        async def task_niche():
            cached = await _get_cached_section(r, user_id, "niche_picks", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_niche_picks_section(
                        user_id, session, tmdb, watched_tmdb_ids.copy(),
                        country_code, local_provider, background_tasks=background_tasks,
                    )
            except Exception as e:
                logger.error(f"Feed Task Failed [Niche]: {e}")
                return None

        async def task_wildcard():
            cached = await _get_cached_section(r, user_id, "wildcard", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_wildcard_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Wildcard]: {e}")
                return None

        async def task_random():
            cached = await _get_cached_section(r, user_id, "random_picks", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_random_recommendations_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Random]: {e}")
                return None

        async def task_hidden():
            cached = await _get_cached_section(r, user_id, "hidden_gems", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_hidden_gems_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, local_provider, background_tasks=background_tasks)
            except Exception as e:
                logger.error(f"Feed Task Failed [Hidden]: {e}")
                return None

        async def task_available():
            cached = await _get_cached_section(r, user_id, "available_now", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    return await self.get_available_now_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, streaming_providers)
            except Exception as e:
                logger.error(f"Feed Task Failed [Available]: {e}")
                return None

        async def task_hybrid():
            cached = await _get_cached_section(r, user_id, "picked_for_you", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_hybrid_picks_section(user_id, session, country_code, watched_tmdb_ids.copy(), local_provider, qdrant=qdrant, background_tasks=background_tasks, redis_client=r)
            except Exception as e:
                logger.error(f"Feed Task Failed [Hybrid]: {e}")
                return None

        async def task_auteur():
            cached = await _get_cached_section(r, user_id, "auteur", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    recommender = RecommendationService(session, tmdb=tmdb, qdrant=qdrant, redis_client=r)
                    return await recommender.get_auteur_section(user_id, country_code, watched_tmdb_ids.copy(), provider_service=local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Auteur]: {e}")
                return None

        async def task_cult_actor():
            cached = await _get_cached_section(r, user_id, "cult_actor", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    recommender = RecommendationService(session, tmdb=tmdb, qdrant=qdrant, redis_client=r)
                    return await recommender.get_cult_actor_section(user_id, country_code, watched_tmdb_ids.copy(), provider_service=local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Cult Actor]: {e}")
                return None

        async def task_upcoming():
            cached = await _get_cached_section(r, user_id, "upcoming", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.engine.get_upcoming_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, local_provider)
            except Exception as e:
                logger.error(f"Feed Task Failed [Upcoming]: {e}")
                return None

        tasks = [
            task_popular(),
            task_hybrid(),
            task_watched(),
            task_hidden(),
            task_niche(),
            task_wildcard(),
            task_random(),
            task_auteur(),
            task_cult_actor(),
            task_available(),
            task_upcoming(),
        ]

        results = await asyncio.gather(*tasks)

        (
            section_popular,
            section_hybrid,
            section_a,
            section_c,
            section_niche,
            section_wildcard,
            section_random,
            section_auteur,
            section_cult_actor,
            section_d,
            section_upcoming,
        ) = results

        # Deduplicate and assemble in display order
        seen_ids: Set[int] = set()
        final_sections = []

        ordered_results = [
            section_popular,
            section_hybrid,
            section_a,
            section_c,
            section_niche,
            section_upcoming,
            section_auteur,
            section_cult_actor,
            section_wildcard,
            section_random,
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
            # Auteur over-collects (up to 21 candidates) so feed-level dedup leaves
            # buffer; here we trim to <=3 per director × <=9 total in score order.
            if unique_items and section.id == "auteur":
                per_director_count: Dict[str, int] = {}
                trimmed: List = []
                for item in unique_items:
                    director = None
                    if item.contributors:
                        director = (item.contributors[0] or {}).get("director")
                    if per_director_count.get(director, 0) >= 3:
                        continue
                    trimmed.append(item)
                    per_director_count[director] = per_director_count.get(director, 0) + 1
                    if len(trimmed) >= 9:
                        break
                unique_items = trimmed
            # Same pattern for cult_actor: <=3 per actor × <=9 total.
            if unique_items and section.id == "cult_actor":
                per_actor_count: Dict[str, int] = {}
                trimmed: List = []
                for item in unique_items:
                    actor = None
                    if item.contributors:
                        actor = (item.contributors[0] or {}).get("actor")
                    if per_actor_count.get(actor, 0) >= 3:
                        continue
                    trimmed.append(item)
                    per_actor_count[actor] = per_actor_count.get(actor, 0) + 1
                    if len(trimmed) >= 9:
                        break
                unique_items = trimmed
            if unique_items:
                section.items = unique_items
                final_sections.append(section)


        final_resp = FeedResponse(feed=final_sections, status="ok")

        # --- CACHE SAVE BLOCK ---
        if r:
            try:
                # Defense: only cache if feed is "complete" (>= 3 sections)
                # to avoid poisoning cache during cold starts/warmups.
                if len(final_sections) >= 3:
                    for section in ordered_results:
                        if section and section.items:
                            await _cache_section(r, user_id, section, country_code, prov_str)
                    logger.info(f"Per-section cache saved for User {user_id}")
                else:
                    logger.warning(
                        f"Feed too thin ({len(final_sections)} sections) for User {user_id}. SKIPPING CACHE."
                    )
            except Exception as e:
                logger.warning(f"Redis feed cache write failed: {e}")
            finally:
                # Only close clients we created locally — the injected lifespan
                # singleton is shared across all requests and must remain open.
                if r_is_local:
                    await r.aclose()
        # --- END CACHE SAVE ---

        return final_resp