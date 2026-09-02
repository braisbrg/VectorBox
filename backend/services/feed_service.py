import logging
from datetime import datetime, timedelta, timezone
import asyncio
import redis.asyncio as aioredis
from typing import List, Dict, Set, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, or_

from config import AsyncSessionLocal, REDIS_URL, FEED_CACHE_VERSION
from models.database import User, UserRating, Movie, MovieAvailability
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
    # 6h y no 24h: la fila dice "te quedan N días" y ese número cambia a medianoche.
    "leaving_soon":        21600,
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


async def get_cached_rank(user_id: int, tmdb_id: int) -> Optional[tuple[int, int]]:
    """Rank of a film inside the user's cached `picked_for_you` pool → (rank, pool).

    TTL-bounded honesty: there is no persisted daily ranking — this reads the
    live feed cache (any country/provider variant, first hit) and returns None
    on a cache miss or if the film isn't in that pool. Powers /why's rank fill
    and the feed hero's "#1 TODAY" badge. Never raises.
    """
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        pattern = f"section:{FEED_CACHE_VERSION}:{user_id}:picked_for_you:*"
        async for key in r.scan_iter(match=pattern, count=50):
            cached = await r.get(key)
            if not cached:
                continue
            try:
                section = FeedSection.model_validate_json(cached)
            except Exception:
                continue
            for idx, item in enumerate(section.items):
                if item.id == tmdb_id:
                    return idx + 1, len(section.items)
            return None  # pool found, film not ranked in it
        return None
    except Exception:
        return None
    finally:
        try:
            await r.close()
        except Exception:
            pass


async def get_cached_feed_tmdb_ids(user_id: int) -> set:
    """All tmdb_ids currently present in the user's CACHED feed sections (any
    section/country/provider variant). Used by reroll endpoints so a reroll
    never repeats a film the feed is already showing. Best-effort: an expired
    cache yields an empty set (no exclusion). Never raises.
    """
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    ids: set = set()
    try:
        pattern = f"section:{FEED_CACHE_VERSION}:{user_id}:*"
        async for key in r.scan_iter(match=pattern, count=50):
            cached = await r.get(key)
            if not cached:
                continue
            try:
                section = FeedSection.model_validate_json(cached)
            except Exception:
                continue
            ids.update(item.id for item in section.items if item.id)
        return ids
    except Exception:
        return ids
    finally:
        try:
            await r.close()
        except Exception:
            pass


# F8: in a filtered feed, keep any row that still has at least this many matches (so a
# WIDE filter shows many rows, a tight one shows few) — instead of pre-deciding which
# rows to show. A 1-2 film carousel reads as broken, hence 3, not 1.
MIN_FILTERED_SECTION_ITEMS = 3

# F8: after output-filtering, cap the deepened wide rows back to their LIVE display
# sizes so a filtered feed reads exactly like the normal feed. Other rows are never
# deepened, so no cap needed.
FILTERED_ROW_CAPS = {"picked_for_you": 10, "because_you_watched": 15, "hidden_gems": 10}


async def _provider_allowed_tmdb_ids(provider_ids, country):
    """F8: tmdb_ids of films available (flatrate/free) on ANY of `provider_ids` in
    `country`, from Postgres MovieAvailability. Providers aren't a Qdrant payload field,
    so we resolve them to an id set and push THAT into each wide row's vector search —
    a taste-rank WITHIN the provider catalogue, not a post-filter of a small pool."""
    wanted = set(provider_ids)
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(MovieAvailability.providers, Movie.tmdb_id)
            .join(Movie, Movie.id == MovieAvailability.movie_id)
            .where(MovieAvailability.country_code == country)
            # Sin cota de frescura, igual que `available_on` — ver el porqué allí:
            # con la tabla sin refrescar, acotar deja el feed de ES en 7 de 6.038.
        )).all()
    return [tmdb_id for provs, tmdb_id in rows
            if provs and any(p.get("provider_id") in wanted for p in provs)]


async def _watchlist_tmdb_ids(user_id: int) -> List[int]:
    """tmdb_ids de la watchlist SIN ver. Lo de "sin ver" no es cosmético: al importar
    un ZIP, una película vista que siguiera en la lista de Letterboxd conserva las dos
    marcas, y sin el `is_watched` esta lista propondría cosas ya vistas."""
    async with AsyncSessionLocal() as session:
        return list((await session.execute(
            select(Movie.tmdb_id)
            .join(UserRating, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.is_watchlist.is_(True))
            .where(UserRating.is_watched.is_(False))
        )).scalars().all())


async def _post_filter_sections(sections, filters, provider_filter, country, tmdb, wl_ids=None):
    """F8 backstop: enforce the rail constraints on every item of the built sections.
    The 3 wide rows filter AT SOURCE (robustness); every other row builds normally and
    is filtered here — provider availability too (not a Qdrant payload field). Rows left
    with < MIN_FILTERED_SECTION_ITEMS matches are dropped, so the feed keeps exactly the
    rows that still have enough films. No-op when unfiltered (normal feed untouched)."""
    if not filters and not provider_filter and wl_ids is None:
        return sections
    all_tmdb = {it.id for s in sections for it in s.items}
    if not all_tmdb:
        return sections

    f = filters or {}
    ymin, ymax = f.get("year_min"), f.get("year_max")
    maxrt, minvbs = f.get("max_runtime"), f.get("min_vectorbox_score")
    genres = set(f.get("include_genres") or [])
    wanted = set(provider_filter or [])
    # Mood: las filas anchas ya vienen filtradas de origen, pero las estrechas
    # (auteur/actor/niche/popular/wildcard) se construyen sin filtros y sin esto
    # colarían películas de otro ánimo en un feed que el usuario pidió de uno.
    grav_min, grav_max = f.get("mood_gravedad_min"), f.get("mood_gravedad_max")
    hum_min, hum_max = f.get("mood_humanidad_min"), f.get("mood_humanidad_max")
    mood_votes, mood_vbs = f.get("mood_min_votes"), f.get("mood_min_vbs")
    mood_votes_max = f.get("mood_max_votes")

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(Movie).where(Movie.tmdb_id.in_(all_tmdb)))).scalars().all()
        meta = {m.tmdb_id: m for m in rows}
        prov_of: Dict[int, Set[int]] = {}
        if wanted:
            psvc = ProviderService(session, tmdb)
            pmap = await psvc.get_providers_batch([m.id for m in rows], country or "ES")
            prov_of = {m.tmdb_id: {p["provider_id"] for p in pmap.get(m.id, [])} for m in rows}

    def ok(it) -> bool:
        # Tres filas se construyen sin filtro ninguno (popular_letterboxd, available_now
        # y upcoming), así que la watchlist las corta aquí o se colarían películas que
        # no están en la lista dentro de un feed que el usuario pidió de su lista.
        if wl_ids is not None and it.id not in wl_ids:
            return False
        m = meta.get(it.id)
        if m is None:
            return False  # can't verify an unknown film under active filters → drop
        if ymin and (m.year is None or m.year < ymin): return False
        if ymax and (m.year is None or m.year > ymax): return False
        if maxrt and (m.runtime is None or m.runtime > maxrt): return False
        if minvbs and (m.vectorbox_score is None or m.vectorbox_score < minvbs): return False
        if genres and not (set(m.genres or []) & genres): return False
        # `is not None` en el umbral: un mínimo de 0 es válido. Y una película sin
        # mood calculado se cae bajo filtro activo, igual que una sin año.
        if grav_min is not None and (m.mood_gravedad is None or m.mood_gravedad < grav_min): return False
        if grav_max is not None and (m.mood_gravedad is None or m.mood_gravedad > grav_max): return False
        if hum_min is not None and (m.mood_humanidad is None or m.mood_humanidad < hum_min): return False
        if hum_max is not None and (m.mood_humanidad is None or m.mood_humanidad > hum_max): return False
        if mood_votes is not None and (m.vote_count or 0) < mood_votes: return False
        if mood_votes_max is not None and (m.vote_count or 0) >= mood_votes_max: return False
        if mood_vbs is not None and (m.vectorbox_score is None or m.vectorbox_score < mood_vbs): return False
        if wanted and not (wanted & prov_of.get(it.id, set())): return False
        return True

    out = []
    for s in sections:
        kept = [it for it in s.items if ok(it)]
        cap = FILTERED_ROW_CAPS.get(s.id)
        s.items = kept[:cap] if cap else kept
        if len(s.items) >= MIN_FILTERED_SECTION_ITEMS:
            out.append(s)
    return out


class FeedService:
    def __init__(self, qdrant: QdrantService = None, embedding_service: EmbeddingService = None):
        self.engine = RecommendationEngine(qdrant=qdrant, embedding_service=embedding_service)

    @safe_execution(fallback_return=FeedSection(id="because_you_watched", title="Recommended for You", items=[]))
    async def get_because_you_watched_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, qdrant: QdrantService, seen_ids: Set[int], country: str, provider_service: ProviderService = None, background_tasks = None, precomputed_anti_vector = None, filters: Dict = None, pool_limit: int = None, include_shorts: bool = False
    ) -> FeedSection:
        return await self.engine.get_because_you_watched_section(user_id, db, tmdb, qdrant, seen_ids, country, provider_service, background_tasks=background_tasks, precomputed_anti_vector=precomputed_anti_vector, filters=filters, pool_limit=pool_limit, include_shorts=include_shorts)

    @safe_execution(fallback_return=FeedSection(id="niche_picks", title="Niche Picks", items=[]))
    async def get_niche_picks_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, provider_service: ProviderService = None, filters: Dict = None, include_shorts: bool = False
    ) -> FeedSection:
        return await self.engine.get_niche_picks_section(user_id, db, tmdb, seen_ids, country, provider_service, filters=filters, include_shorts=include_shorts)

    @safe_execution(fallback_return=FeedSection(id="hidden_gems", title="Hidden Gems", items=[]))
    async def get_hidden_gems_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, provider_service: ProviderService = None, filters: Dict = None, pool_limit: int = None, include_shorts: bool = False
    ) -> FeedSection:
        return await self.engine.get_hidden_gems_section(user_id, db, tmdb, seen_ids, country, provider_service, filters=filters, pool_limit=pool_limit, include_shorts=include_shorts)

    @safe_execution(fallback_return=FeedSection(id="available_now", title="Available on Your Services", items=[]))
    async def get_available_now_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, streaming_providers: List[int]
    ) -> FeedSection:
        return await self.engine.get_available_now_section(user_id, db, tmdb, seen_ids, country, streaming_providers)



    @safe_execution(fallback_return=None)
    async def get_wildcard_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, provider_service: ProviderService = None, filters: Dict = None, include_shorts: bool = False
    ) -> Optional[FeedSection]:
        return await self.engine.get_wildcard_section(user_id, db, tmdb, seen_ids, country, provider_service, filters=filters, include_shorts=include_shorts)

    @safe_execution(fallback_return=None)
    async def get_random_recommendations_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, seen_ids: Set[int], country: str, provider_service: ProviderService = None, filters: Dict = None, include_shorts: bool = False
    ) -> Optional[FeedSection]:
        return await self.engine.get_random_recommendations_section(user_id, db, tmdb, seen_ids, country, provider_service, filters=filters, include_shorts=include_shorts)

    async def get_popular_on_letterboxd_section(
        self, user_id: int, db: AsyncSession, tmdb: TMDBClient, country: str, provider_service: ProviderService = None, include_shorts: bool = False
    ) -> Optional[FeedSection]:
        return await self.engine.get_popular_on_letterboxd_section(user_id, db, tmdb, country, provider_service, include_shorts=include_shorts)
        
    async def get_hybrid_picks_section(self, user_id: int, db: AsyncSession, country: str, seen_ids: Set[int], provider_service: ProviderService = None, qdrant: QdrantService = None, background_tasks = None, redis_client = None, filters: Dict = None, pool_limit: int = None) -> Optional[FeedSection]:
        tmdb = provider_service.tmdb if provider_service else None
        recommender = RecommendationService(db, tmdb=tmdb, qdrant=qdrant, redis_client=redis_client)
        return await recommender.get_hybrid_picks_section(user_id, country, seen_ids, provider_service, background_tasks=background_tasks, filters=filters, pool_limit=pool_limit)

    async def get_main_feed(
        self,
        user_id: int,
        country_code: str,
        streaming_providers: List[int],
        tmdb: TMDBClient,
        qdrant: QdrantService,
        background_tasks = None,
        redis_client = None,
        filters: Optional[Dict] = None,
        provider_filter: Optional[List[int]] = None,
        only_watchlist: bool = False,
    ) -> FeedResponse:
        """
        Generate the main feed using FULLY PARALLEL EXECUTION.
        Includes high-level Redis caching for blazing fast loads.

        F8: when `filters`/`provider_filter` are set (rail EXECUTE_QUERY), every row is
        built and filtered — the 3 WIDE rows (Trident, Because You Watched, Hidden Gems)
        apply the constraints at their own search; the rest are post-filtered — then any
        row left with < MIN_FILTERED_SECTION_ITEMS matches is dropped. So a wide filter
        keeps many rows, a tight one few. Filtered feeds use a separate cache namespace.
        """
        # --- CACHE INTERCEPT BLOCK ---
        # Prefer the injected lifespan singleton (shared connection pool).
        # Only create a new client if nothing was injected (e.g. direct test calls).
        # r_is_local tracks ownership: we must NOT close the injected singleton or
        # subsequent requests will fail with "connection closed".
        r = redis_client
        r_is_local = False
        prov_str = ",".join(map(str, sorted(streaming_providers)))
        # F8: filtered feeds get their own cache namespace so they never collide with
        # the normal feed. Invalidation SCANs section:* so these are still cleared.
        if filters or provider_filter or only_watchlist:
            import hashlib, json as _json
            _sig = hashlib.md5(
                _json.dumps({"f": filters or {}, "p": sorted(provider_filter or []), "w": only_watchlist}, sort_keys=True).encode()
            ).hexdigest()[:10]
            prov_str = f"{prov_str}|flt:{_sig}"
        if r is None:
            try:
                r = aioredis.from_url(REDIS_URL, decode_responses=True)
                r_is_local = True
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}")
                r = None
        # --- END CACHE INTERCEPT ---

        # Preferencia de cortos: se resuelve UNA vez aquí y viaja como argumento a
        # las filas. Cada sección abre su propia sesión, así que leerla dentro
        # costaría una consulta por fila para un booleano que no cambia durante
        # la construcción del feed. Si falla, el valor seguro es el por defecto.
        include_shorts = False
        try:
            async with AsyncSessionLocal() as session:
                include_shorts = bool(await session.scalar(
                    select(User.include_shorts).where(User.id == user_id)
                ))
        except Exception as e:
            logger.warning(f"include_shorts lookup failed for user_id={user_id}: {e}")

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

        # --- PRE-POPULATE excluded tmdb_ids (watched OR rejected) so every signal excludes them ---
        # BYW (Signal A) and other section builders that lean on this set rather than
        # querying ratings themselves were leaking rejected films back into the feed
        # after F5. Including is_rejected here is the upstream fix — section-level
        # internal queries also got the same filter (round 5), this closes the path
        # for builders that *only* consume the caller-supplied set.
        watched_tmdb_ids: Set[int] = set()
        try:
            async with AsyncSessionLocal() as session:
                excluded_result = await session.execute(
                    select(UserRating.movie_id)
                    .where(UserRating.user_id == user_id)
                    .where(or_(UserRating.is_watched.is_(True), UserRating.is_rejected.is_(True)))
                )
                excluded_internal_ids = set(excluded_result.scalars().all())

                if excluded_internal_ids:
                    movies_result = await session.execute(
                        select(Movie.tmdb_id).where(Movie.id.in_(excluded_internal_ids))
                    )
                    watched_tmdb_ids = set(movies_result.scalars().all())

            logger.info(f"Pre-populated {len(watched_tmdb_ids)} excluded tmdb_ids (watched+rejected) for User {user_id}")
        except Exception as e:
            logger.error(f"Failed to pre-populate excluded tmdb_ids: {e}")
        # --- END PRE-POPULATE ---

        # F8 (Option B — user 2026-07-07): apply the rail's year/runtime/genre/VBS as an
        # OUTPUT filter (_post_filter_sections below) so every row keeps the LIVE feed's
        # ranking with non-matching films simply removed — "same feed, filtered" — NOT
        # re-ranked by baking the constraints into each search (which re-runs the Trident's
        # RRF and reshuffles even films that pass; see the 212 Q≥70 case). The ONE exception
        # is PROVIDERS: their catalogue is too sparse to post-filter (would starve the rows),
        # so they ride into the search as an allowed film-id set (provider-first).
        search_filters = None
        # Las filas DE BASE DE DATOS (niche/wildcard/random) no pueden usar el id-set:
        # ese existe porque Qdrant no tiene los proveedores en el payload. Ellas sí
        # alcanzan `movie_availability` con un EXISTS, así que reciben el filtro en su
        # propia query. Sólo el proveedor: año/género/VBS siguen post-filtrándose ahí,
        # que es la semántica de "el mismo feed, filtrado" que se decidió en F8.
        db_provider_filter = (
            {"provider_ids": provider_filter, "country": country_code} if provider_filter else None
        )
        # WATCHLIST: mismo mecanismo, misma razón. Es el filtro MÁS selectivo de todos
        # —537 de 12.861 para u212, un 4%— así que post-filtrar no dejaría ni una fila
        # en pie. Sustituye al `scope=watchlist` viejo, que en vez de filtrar montaba un
        # feed paralelo de tres ORDER BY y se perdía el motor entero.
        if only_watchlist:
            db_provider_filter = {**(db_provider_filter or {}), "watchlist_user_id": user_id}
        # Las de persona (auteur/actor) SÍ llevan además el resto de filtros del rail —
        # ya lo hacían — y al ir `filters` no vacío se activa de paso el paseo hondo por
        # el ranking de afinidad (FILTERED_PERSON_FALLBACK_DEPTH), que es justo lo que
        # hace falta: baja a un director algo menos afín pero que sí puedes ver.
        db_filters = {**(filters or {}), **(db_provider_filter or {})} or None
        # Las 3 filas anchas van por Qdrant, que no conoce ni proveedores ni watchlist,
        # así que ambos entran como conjunto de ids permitidos. Con los dos activos es
        # la INTERSECCIÓN: "de mi lista, lo que además está en mis servicios".
        conjuntos = []
        if provider_filter:
            try:
                allowed = await _provider_allowed_tmdb_ids(provider_filter, country_code)
                conjuntos.append(set(allowed))
                logger.info(f"F8: provider filter → {len(allowed)} allowed films (source-filtered)")
            except Exception as e:
                logger.warning(f"F8: provider id-set resolve failed ({e}); post-filter only")
        wl_ids: Optional[Set[int]] = None
        if only_watchlist:
            wl_ids = set(await _watchlist_tmdb_ids(user_id))
            conjuntos.append(wl_ids)
            logger.info(f"watchlist scope → {len(wl_ids)} films")
        if conjuntos:
            search_filters = {"include_tmdb_ids": list(set.intersection(*conjuntos))}

        # MOOD: segunda excepción a la regla de "filtro de salida", por las dos
        # razones que hacen excepción a los proveedores.
        #   · Selectividad: cada cuadrante es el ~15-17% del catálogo (medido
        #     2026-08-06), así que post-filtrar un pool de 60 deja ~9 y las filas
        #     rozan MIN_FILTERED_SECTION_ITEMS. Es inanición por post-filtro.
        #   · Semántica: "algo reconfortante" es «dame un feed reconfortante», no
        #     «mi feed de siempre menos lo que no lo sea». Aquí re-rankear SÍ es lo
        #     que se pide, al revés que con el slider de Q.
        # Es barato: es un campo del payload, igual que vectorbox_score.
        mood_at_source = {k: v for k, v in (filters or {}).items() if k.startswith("mood_")}
        if mood_at_source:
            search_filters = {**(search_filters or {}), **mood_at_source}
            logger.info(f"F8: mood filter at source → {mood_at_source}")

        # F8 deep pool: when output filters are active, the 3 wide rows return their
        # normal head PLUS a score-ordered tail (~60 items) so a selective filter still
        # finds enough matches; _post_filter_sections caps rows back to live size.
        deep_pool = 60 if filters else None

        async def task_popular():
            cached = await _get_cached_section(r, user_id, "popular_letterboxd", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    return await self.get_popular_on_letterboxd_section(user_id, session, tmdb, country_code, local_provider, include_shorts=include_shorts)
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
                    return await self.get_because_you_watched_section(user_id, session, tmdb, qdrant, watched_tmdb_ids.copy(), country_code, local_provider, background_tasks=background_tasks, precomputed_anti_vector=precomputed_anti_vector, filters=search_filters, pool_limit=deep_pool, include_shorts=include_shorts)
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
                        country_code, local_provider, filters=db_provider_filter,
                        include_shorts=include_shorts,
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
                    return await self.get_wildcard_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, local_provider, filters=db_provider_filter, include_shorts=include_shorts)
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
                    return await self.get_random_recommendations_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, local_provider, filters=db_filters, include_shorts=include_shorts)
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
                    return await self.get_hidden_gems_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, local_provider, filters=search_filters, pool_limit=deep_pool, include_shorts=include_shorts)
            except Exception as e:
                logger.error(f"Feed Task Failed [Hidden]: {e}")
                return None

        async def task_available():
            cached = await _get_cached_section(r, user_id, "available_now", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    # Igual que `leaving_soon`: los servicios salen del rail si hay filtro
                    # y si no de los ajustes. Esta fila es ANTERIOR a la API de streaming
                    # y no tiene nada que ver con ella — sólo enseña buenas películas que
                    # estén en tus servicios — pero leía únicamente `streaming_providers`,
                    # así que desaparecía para quien los elegía en el rail (2026-08-19).
                    return await self.get_available_now_section(user_id, session, tmdb, watched_tmdb_ids.copy(), country_code, provider_filter or streaming_providers)
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
                    return await self.get_hybrid_picks_section(user_id, session, country_code, watched_tmdb_ids.copy(), local_provider, qdrant=qdrant, background_tasks=background_tasks, redis_client=r, filters=search_filters, pool_limit=deep_pool)
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
                    return await recommender.get_auteur_section(user_id, country_code, watched_tmdb_ids.copy(), provider_service=local_provider, filters=db_filters)
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
                    return await recommender.get_cult_actor_section(user_id, country_code, watched_tmdb_ids.copy(), provider_service=local_provider, filters=db_filters)
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

        async def task_leaving():
            cached = await _get_cached_section(r, user_id, "leaving_soon", country_code, prov_str)
            if cached:
                return cached
            try:
                async with AsyncSessionLocal() as session:
                    local_provider = ProviderService(session, tmdb)
                    # Los servicios salen del rail SI hay filtro activo, y si no de los
                    # ajustes. La fila se condiciono solo al rail y por eso era invisible
                    # para quien los elige en settings, que es donde los elige todo el
                    # mundo (reportado 2026-08-19). Ojo: esto NO convierte los ajustes en
                    # un filtro del feed — settings significa "ensename una fila", el rail
                    # significa "filtra todo", y esa distincion se mantiene.
                    servicios = provider_filter or streaming_providers
                    if not servicios:
                        return None
                    f = {**(db_filters or {}), "provider_ids": servicios, "country": country_code}
                    return await self.engine.get_leaving_soon_section(session, tmdb, watched_tmdb_ids.copy(), country_code, local_provider, filters=f)
            except Exception as e:
                logger.error(f"Feed Task Failed [Leaving Soon]: {e}")
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
            task_leaving(),
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
            section_leaving,
        ) = results

        # Deduplicate and assemble in display order
        seen_ids: Set[int] = set()
        final_sections = []

        ordered_results = [
            section_popular,
            section_hybrid,
            section_a,
            # ARRIBA del todo entre las de descubrimiento, no a mitad: el dedup entre
            # filas lo gana la de más arriba, y ésta es la única que CADUCA. Estuvo la
            # quinta y bajo filtro del rail desaparecía — las filas de arriba, acotadas
            # al mismo puñado de proveedores, se comían sus 4 películas y el mínimo de 3
            # la tiraba (reportado 2026-08-19 con Prime+Disney+Filmin). Perder una
            # película suya por una fila que seguirá ahí dentro de un mes es el reparto
            # equivocado.
            section_leaving,
            section_c,
            section_niche,
            section_upcoming,
            section_auteur,
            section_cult_actor,
            section_wildcard,
            section_random,
            section_d,
        ]

        # F8: filter + cap rows BEFORE the cross-row dedup — a deepened row's
        # never-displayed tail must not eat films out of the later rows.
        if filters or provider_filter or only_watchlist:
            ordered_results = await _post_filter_sections(
                [s for s in ordered_results if s], filters, provider_filter, country_code, tmdb, wl_ids
            )

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
            # The MIN_FILTERED_SECTION_ITEMS gate lives in _post_filter_sections,
            # which runs BEFORE this cross-row dedup — deliberately, so a deepened
            # row's never-displayed tail cannot eat films out of later rows. The
            # consequence was not: dedup then takes items OUT of rows that already
            # cleared the gate, and nothing re-checked. Reproduced 3/3 passes at
            # min_vectorbox_score=80, where "Because You Watched" cleared with 4
            # and shipped with 2. Re-check here, under filters only — an
            # unfiltered row is built to size and a short one is legitimate.
            if filters or provider_filter or only_watchlist:
                keep = len(unique_items) >= MIN_FILTERED_SECTION_ITEMS
            else:
                keep = bool(unique_items)
            if keep:
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
                # `aclose()` is the redis-py 5.x name; we're on 4.x so the
                # async client only exposes `close()`. See backend/requirements.txt.
                if r_is_local:
                    await r.close()
        # --- END CACHE SAVE ---

        return final_resp