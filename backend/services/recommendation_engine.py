import logging
import random
import asyncio
import math
import functools
from collections import Counter
from datetime import datetime, timezone, date, timedelta
from typing import List, Dict, Set, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, or_
import numpy as np

from utils.anti_vector import most_anti_similar
from utils.scoring import normalize_film_similarity_score

from models.database import UserRating, Movie, UserCluster, MovieAvailability
from models.schemas import FeedSection, FeedItem
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.clustering_service import ClusteringService
from services.provider_service import ProviderService
from services.recommendation_service import RecommendationService
from services.embedding_service import EmbeddingService
from services.movie_service import MovieService
from services.trending_service import TrendingService
from config import REDIS_URL
import time

from opentelemetry import trace
from telemetry import get_tracer

logger = logging.getLogger(__name__)
_tracer = get_tracer("recommendation_engine")


async def _ingest_movie_background(tmdb_id: int) -> None:
    """Background-safe movie ingestion: owns its own session, never re-raises.

    Reuses the TMDB singleton (a bare MovieService(session) builds its own
    TMDBClient) and closes the service so the lazily-created OMDb/Qdrant
    clients don't leak one connection pool per ingested movie.
    """
    from config import AsyncSessionLocal
    from dependencies import get_tmdb_client
    tmdb = await get_tmdb_client()
    async with AsyncSessionLocal() as session:
        movie_service = MovieService(session, tmdb=tmdb)
        try:
            await movie_service.get_or_create_movie(tmdb_id)
            await session.commit()
        except Exception as e:
            logger.error(f"Background auto-ingest failed for tmdb_id={tmdb_id}: {e}")
        finally:
            await movie_service.close()

def available_on(provider_ids, country: str):
    """EXISTS: the film is on a subscription in `country` on any of `provider_ids`.

    `@>` over the JSONB array, one OR per provider — a user picks 2-5 services, so
    the OR list is short. No index: 14.589 ES rows scan in ~4 ms, and adding a GIN
    for that would cost more to maintain than it saves.
    """
    return (
        select(1)
        .where(MovieAvailability.movie_id == Movie.id)
        .where(MovieAvailability.country_code == country)
        # SIN cota de frescura, y es deliberado — se probó y se revirtió el
        # 2026-09-01. Acotar a CACHE_DURATION_DAYS (7) dejaba el feed de ES en
        # **7 películas de 6.038**: toda la disponibilidad de ES es UN volcado de
        # hace ~15-21 días y nadie la refresca, así que la cota no filtraba lo
        # rancio, lo borraba todo. Coste por TTL, medido: 7d→7, 14d→64, 21d→6.038.
        # Una cota aquí sólo es segura si algo refresca la tabla con esa cadencia;
        # mientras no lo haya, vaciaría la fila sola y en silencio según envejece.
        # El daño que evitaría es 49 filas afirmando de más — mucho menor. Ver
        # BACKLOG: lo que hay que arreglar es el refresco, no la lectura.
        .where(or_(*[MovieAvailability.providers.contains([{"provider_id": int(p)}])
                     for p in provider_ids]))
        .exists()
    )


def in_watchlist(user_id: int):
    """EXISTS: la película está en la watchlist del usuario y sin ver.

    El filtro más selectivo del rail (4% del catálogo), y por eso el que peor
    aguantaría un post-filtro. Aquí decide QUÉ elige cada fila, no qué se le quita
    después: "joyas ocultas DE MI LISTA" en vez de "joyas ocultas, menos las que no
    estén en mi lista", que casi siempre se quedaba en cero.
    """
    return (
        select(1)
        .where(UserRating.movie_id == Movie.id)
        .where(UserRating.user_id == user_id)
        .where(UserRating.is_watchlist.is_(True))
        .where(UserRating.is_watched.is_(False))
        .exists()
    )


def apply_rail_filters(q, filters: Optional[Dict]):
    """Push the rail's constraints into a row's own query.

    Post-filtering can only subtract from a choice the row already made without
    knowing the filter. Measured 2026-07-29 on real users, asking for 9 films:

        auteur  u210 VBS>=80     post-filter 5/9  ->  at source 9/9  (VBS 85.4 -> 85.0)
                u210 Drama 90s   post-filter 2/9  ->  at source 9/9  (VBS 71.0 -> 71.2)
                u212 Drama 90s   post-filter 4/9  ->  at source 9/9  (VBS 76.8 -> 76.2)
        random  VBS>=80          post-filter 2.2/12 -> at source 12/12

    Quality holds because the pool is large enough that the filter selects rather
    than starves. `niche` (fetches 60, shows 9) and `wildcard` (50 for 10) were
    measured too and gain nothing — they already carry the headroom — so they
    keep post-filtering only.

    Streaming providers were deliberately absent while `movie_availability` only
    filled up REACTIVELY (whoever opened a film cached it), because a join against
    a table with holes silently drops films that are on the service but unread.
    The daily refresh now writes it for the whole catalogue — 14.589 rows in ES,
    86% of them under a day old (2026-08-17) — so it IS reachable, and providers
    join here as an EXISTS.

    They needed it more than the other filters, and for a different reason: a
    filter that keeps ~2/3 of the catalogue selects, one that keeps a third
    starves. Measured with Netflix+Prime+Disney: niche 20 -> 3 films, wildcard
    10 -> 3, random 10 -> row dropped. That is why the "niche and wildcard carry
    enough headroom" note above does not extend to this one.

    _post_filter_sections stays as the backstop if a row forgets one of these.
    """
    if not filters:
        return q
    if filters.get("provider_ids"):
        q = q.where(available_on(filters["provider_ids"], filters.get("country") or "ES"))
    if filters.get("watchlist_user_id"):
        q = q.where(in_watchlist(filters["watchlist_user_id"]))
    if filters.get("year_min"):
        q = q.where(Movie.year >= filters["year_min"])
    if filters.get("year_max"):
        q = q.where(Movie.year <= filters["year_max"])
    if filters.get("max_runtime"):
        q = q.where(Movie.runtime <= filters["max_runtime"])
    if filters.get("min_vectorbox_score"):
        q = q.where(Movie.vectorbox_score >= filters["min_vectorbox_score"])
    if filters.get("include_genres"):
        q = q.where(Movie.genres.overlap(filters["include_genres"]))
    return q


# How far down the affinity ranking the auteur / cult-actor rows may walk when a
# filter is active. Unfiltered they stop at people 4-10, which is plenty; under a
# filter the measured worst case reached #16 (Fritz Lang for user 210 at
# <100 min) and the films were still ones that user demonstrably likes.
FILTERED_PERSON_FALLBACK_DEPTH = 15
PERSON_FALLBACK_DEPTH = 7

# Minimum quality requirements for any movie to appear in recommendations.
# Honoured by every discovery surface (feed engine, Magic Box, onboarding
# carousel) and intentionally NOT consulted by user-chosen surfaces
# (watchlist, watched history, direct movie lookup, title autocomplete) —
# so flagged non-films / nicheless films stay accessible if the user added
# them deliberately.
_MOVIE_QUALITY_GATE = [
    Movie.vote_count >= 10,
    Movie.year.isnot(None),
    Movie.vectorbox_score.isnot(None),
    Movie.is_excluded.is_(False),
    # TMDB `adult` titles. The catalogue is curated so these shouldn't exist
    # (post_change_smoke asserts count 0), but ANY signed-in user can push a
    # row into the shared catalogue via POST /movies/{id}/rate → ingest, and
    # the random/wildcard rows serve the whole catalogue at func.random() to
    # every other user. Curation is not an access control — filter here too.
    Movie.is_adult.is_(False),
]

# Umbral de cortometraje — definición de la Academia (<= 40 min, créditos
# incluidos). Deliberadamente NO se filtra por el género `TV Movie` de TMDB:
# las 211 entradas que lo llevan son cine legítimo (Duel de Spielberg, el
# Decálogo de Kieślowski, Saraband de Bergman, The Fog of War) y el Q ya las
# separa solo — van de 12.5 (The Star Wars Holiday Special) a 91.3. El catálogo
# entero viene de Letterboxd, que ya excluye las series de su universo.
SHORT_FILM_MAX_RUNTIME = 40


def movie_quality_gate(include_shorts: bool = False) -> list:
    """Cláusulas del gate de calidad para una superficie de descubrimiento.

    `include_shorts` sale de `User.include_shorts` (ajustes). Por defecto los
    cortos quedan fuera: el Q no distingue formato, así que Paperman (7 min,
    93.4) y Piper (6 min, 94.9) compiten de tú a tú con un largo en la misma
    fila. Son 1203 en el catálogo, 800 por encima del gate.

    Duración desconocida NO es un corto: tratarla como tal borraría del feed
    películas que nadie pidió quitar. Y el centinela de «no se sabe» aquí es el
    **0**, no el NULL — medido 2026-08-18: 0 filas con `runtime IS NULL` y 505
    con `runtime = 0`, 21 de ellas por encima del gate de calidad. La primera
    versión de esta función sólo contemplaba NULL y se las comía en silencio; el
    test no lo vio porque leía el SQL generado en vez de contar filas. La rama
    NULL se queda porque la columna es nullable y mañana puede volver a haberlos.
    """
    if include_shorts:
        return list(_MOVIE_QUALITY_GATE)
    return _MOVIE_QUALITY_GATE + [
        or_(
            Movie.runtime.is_(None),
            Movie.runtime <= 0,
            Movie.runtime > SHORT_FILM_MAX_RUNTIME,
        )
    ]

# Because You Watched quality floor. VBS — NOT a vote-count floor — is the right
# lever here (measured 2026-07-06). A vote-count floor is a popularity proxy that
# cuts the BEST obscure matches (Edward Yang's Taipei Story, Rohmer, Ghibli shorts
# — cos 0.74-0.79, VBS 61-72) while VBS already sorts quality: the real junk in the
# raw neighbours is VBS=None phantom entries (removed by movie_quality_gate) and
# low-VBS popular films (Twilight VBS 30 < 55). VBS's Bayesian shrinkage means a
# low-vote film that still scores high has genuinely strong ratings. 55→60 trims
# the weak tail (El verano 55.0, Mirrored Mind 57.7, Carrie Pilby 57.1) while
# keeping the obscure-but-excellent matches a vote floor would have killed.
BYW_MIN_VBS = 60
# Cuánto se penaliza al decil más parecido al anti-vector. 0.6 era el factor
# del tramo suave de BYW y se conserva: lo que cambió el 2026-09-01 es QUIÉN
# entra, no cuánto se le baja.
BYW_ANTI_VECTOR_DEMOTE_FACTOR = 0.6

# Global evocative themes rotating independently of user clusters
GLOBAL_THEMES = [
    {
        "id": "sleep_optional",
        "title": "Sleep Optional",
        "include_genres": ["Horror", "Thriller"],
        "require_any": ["Horror"],
        "exclude_genres": ["Family", "Animation", "Comedy"],
        "min_score": 65,
        "min_votes": 50,
    },
    {
        "id": "comfort_watch",
        "title": "Comfort Watch",
        "include_genres": ["Comedy", "Romance", "Animation"],
        "require_any": ["Comedy"],
        "exclude_genres": ["Horror", "War", "Crime"],
        "min_score": 65,
        "min_votes": 50,
    },
    {
        "id": "your_brain_called",
        "title": "Your Brain Called",
        "include_genres": ["Science Fiction", "Mystery", "Thriller"],
        "exclude_genres": ["Family", "Animation"],
        "min_score": 65,
        "min_votes": 50,
    },
    {
        "id": "parents_havent_seen",
        "title": "Your Parents Haven't Seen Either",
        "include_genres": ["Drama", "Crime", "Western"],
        "exclude_genres": [],
        "min_score": 68,
        "min_votes": 100,
        "max_year": 1990,
        "max_popularity": 30,
    },
    {
        "id": "slow_burn",
        "title": "Slow Burn",
        "include_genres": ["Drama", "Mystery", "Thriller"],
        "exclude_genres": ["Family", "Animation", "Comedy"],
        "min_score": 65,
        "min_votes": 50,
        "min_runtime": 130,
    },
    {
        "id": "beautiful_chaos",
        "title": "Beautiful Chaos",
        "include_genres": ["Action", "Crime", "Adventure"],
        "exclude_genres": ["Family", "Animation"],
        "min_score": 65,
        "min_votes": 50,
    },
    {
        "id": "bring_tissues",
        "title": "Bring Tissues",
        "include_genres": ["Drama", "War", "History"],
        "exclude_genres": ["Comedy", "Animation"],
        "min_score": 65,
        "min_votes": 50,
    },
    {
        "id": "subtitles_required",
        "title": "Subtitles Required",
        "include_genres": ["Drama", "Romance", "Crime"],
        "exclude_genres": [],
        "min_score": 65,
        "min_votes": 50,
        "original_language_not": "en",
    },
    {
        "id": "based_on_true_crime",
        "title": "Based on True Crime",
        "include_genres": ["Crime", "Thriller", "Drama"],
        "exclude_genres": ["Family", "Animation"],
        "min_score": 68,
        "min_votes": 100,
    },
]

# Genres too generic to be useful discriminators for cluster filtering
GENERIC_GENRES = {"Action", "Drama", "Comedy", "Adventure", "Thriller"}

# Genre exclusion pairs: (movie_must_not_have, unless_cluster_has)
# If a movie has a niche genre the cluster doesn't, exclude it
EXCLUSION_PAIRS = [
    ({"Family", "Animation"}, {"Family", "Animation"}),
    ({"Documentary"}, {"Documentary"}),
    ({"Horror"}, {"Horror"}),
    ({"Musical", "Music"}, {"Musical", "Music"}),
    ({"Western"}, {"Western"}),
]


def _movie_filter_clauses(filters: Dict = None) -> list:
    """F8: translate the rail filter dict (Qdrant key names) into SQLAlchemy WHERE
    clauses for the DB-driven wide sections. Empty list when unfiltered → no-op in
    `.where(*[])`, so the normal feed is untouched."""
    if not filters:
        return []
    clauses = []
    if filters.get("year_min"):
        clauses.append(Movie.year >= filters["year_min"])
    if filters.get("year_max"):
        clauses.append(Movie.year <= filters["year_max"])
    if filters.get("max_runtime"):
        clauses.append(Movie.runtime.isnot(None))
        clauses.append(Movie.runtime <= filters["max_runtime"])
    if filters.get("min_vectorbox_score"):
        clauses.append(Movie.vectorbox_score >= filters["min_vectorbox_score"])
    if filters.get("include_genres"):
        clauses.append(Movie.genres.overlap(filters["include_genres"]))
    # F8: provider-resolved allowed id set (see qdrant_service include_tmdb_ids).
    if filters.get("include_tmdb_ids"):
        clauses.append(Movie.tmdb_id.in_(filters["include_tmdb_ids"]))
    return clauses


def _get_signal_c_thresholds(user_movie_count: int) -> dict:
    """Return dynamic thresholds for Signal C based on user's rated/liked movie count."""
    if user_movie_count < 30:
        # Cold start — very permissive
        return {
            "min_score": 60,
            "max_popularity": 40,
            "min_votes": 200,
        }
    elif user_movie_count < 100:
        # Growing profile — balanced
        return {
            "min_score": 65,
            "max_popularity": 30,
            "min_votes": 300,
        }
    else:
        # Rich profile — full fidelity
        return {
            "min_score": 70,
            "max_popularity": 35,
            "min_votes": 300,
        }

def _score_anchor_candidate(rating, watched_date, now, watch_count: int = 1,
                            neutral_decay: float = 0.5) -> float:
    """
    Combine rating quality with recency decay and rewatch boost.
    Half-life: 730 days. No floor — decay is unbounded so recent high-rated films
    correctly beat old liked-only films even when all history is 2-5 years old.
    Handles rating=None (liked-only movies) by defaulting to 3.5.

    `neutral_decay` es lo que vale una fila SIN `watched_date`. Era un `days_ago = 730`
    fijo, o sea 0.5 clavado, y eso **castiga el dato ausente**: medido el 2026-08-11 sobre
    user 212, después de que reimportar su ZIP dejara al 51% de su biblioteca sin fecha,
    Fight Club con un 5.0 puntuaba **0.500** mientras Spider-Man: Brand New Day con un 4.5
    reciente sacaba **0.895**. BYW anclaba en lo último visto por goleada, y devolvía seis
    películas de Spider-Man. El llamador pasa ahora la MEDIANA del decay de las filas
    fechadas del propio usuario, que se calibra sola y baja con el tiempo igual que el
    resto — misma corrección que en `clustering_service` (ver su bloque de `neutro`).
    """
    from datetime import timezone
    effective_rating = rating if rating is not None else 3.5

    if watched_date and watched_date.tzinfo is None:
        watched_date = watched_date.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if watched_date:
        decay = 0.5 ** (max(0, (now - watched_date).days) / 730)
    else:
        decay = neutral_decay
    rewatch_boost = min(1.0 + (watch_count - 1) * 0.15, 1.4)
    return (effective_rating / 5.0) * decay * rewatch_boost


# Hidden Gems: how known is too known, and how wide the affinity pool is.
# 100k IMDb votes measured best of 50k/100k/200k — 50k narrows the universe so
# hard that different users start sharing the row again (overlap 5.0/10 vs 1.3).
SIGNAL_C_MAX_VOTES = 100_000
SIGNAL_C_POOL = 600  # over-fetch: the vote ceiling is applied after this pool

# On Your Radar: how many slots go to "what is coming" before "what is coming
# for you" takes over. 5 makes the row read like the old popularity-only one;
# 2 and 3 measure the same, 3 keeps a fuller headline.
RADAR_HEADLINES = 3

# Prior de década para las filas de mood (niche_picks). Suavizado: cuanto más alto,
# más suave. 4.0 medido el 2026-08-11 — ver el bloque en get_niche_picks_section.
NICHE_ERA_ALPHA = 4.0



def _apply_exoticism_boost(score: float, original_language: str) -> float:
    """Boost non-English films by 15% in Hidden Gems section."""
    if original_language and original_language != "en":
        return min(score * 1.15, 1.0)
    return score


def _director_weight(rating: float) -> float:
    """Weighted point system for director/actor auteur activation."""
    if rating >= 4.5: return 2.0
    if rating >= 4.0: return 1.5
    if rating >= 3.5: return 1.0
    if rating >= 3.0: return 0.5
    if rating >= 2.5: return 0.2
    return 0.0


class RecommendationEngine:
    """
    Core engine for generating recommendation strategies.
    Decoupled from the FeedService orchestration layer.
    """

    def __init__(self, qdrant: QdrantService = None, embedding_service: EmbeddingService = None):
        self.qdrant = qdrant
        self.clustering = ClusteringService(qdrant=qdrant)
        self.embedding_service = embedding_service
        if self.embedding_service is None:
            logger.warning("EmbeddingService not injected into RecommendationEngine.")

    async def _get_anti_vector(self, user_id: int, db: AsyncSession, qdrant: QdrantService) -> Optional[list]:
        """Delegates to `utils.anti_vector.compute_anti_vector`.

        Kept as a thin instance method so existing call sites
        (`self._get_anti_vector(...)`) and the test suite don't have to
        change. The full policy and rationale live in the utility.
        """
        from utils.anti_vector import compute_anti_vector
        return await compute_anti_vector(user_id, db, qdrant)

    async def _get_genre_fallback_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None,
        include_shorts: bool = False,
    ) -> FeedSection:
        """
        Cold start fallback: recommend high-scoring films from user's dominant genres.
        Used when Signal A or B produce empty results.
        """
        # Top-3 most-loved genres from user's actual ratings (rating-weighted).
        # Going direct to ratings instead of cluster.dominant_genres avoids the
        # "biggest cluster ≠ most loved" problem — see
        # scripts/experiment_feed_sections.py for the user 210 case where
        # Drama dominates by weight but Action led the biggest cluster.
        from services.clustering_service import ClusteringService
        prefs = await ClusteringService.get_user_genre_preferences(user_id, db)
        fallback_genres = [g for g, _ in prefs[:3]] or ["Drama", "Thriller"]

        # Get watched + rejected movie IDs to exclude
        excluded_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.is_watched.is_(True), UserRating.is_rejected.is_(True)))
        )
        excluded_ids = set(excluded_result.scalars().all())

        # Query DB for high-score unseen films matching genres (array overlap)
        candidates_result = await db.execute(
            select(Movie)
            .where(*movie_quality_gate(include_shorts))
            .where(
                Movie.vectorbox_score > 70,
                Movie.genres.overlap(fallback_genres)
            )
            .order_by(desc(Movie.vectorbox_score))
            .limit(200)
        )
        candidates = candidates_result.scalars().all()

        # Filter excluded and seen
        filtered = [m for m in candidates if m.tmdb_id not in seen_ids and m.id not in excluded_ids][:10]

        if not filtered:
            return FeedSection(id="genre_fallback", title="Recommended for You", items=[])

        # Batch provider fetch
        if provider_service and filtered:
            filt_ids = [m.id for m in filtered]
            providers_map = await provider_service.get_providers_batch(filt_ids, country)
        else:
            providers_map = {}

        items = []
        for movie in filtered:
            p_data = providers_map.get(movie.id, [])
            s_providers = [p["provider_name"] for p in p_data]
            item = await self.create_feed_item(
                movie, None, country, tmdb,
                provider_service=provider_service,
                streaming_providers=s_providers
            )
            items.append(item)
            seen_ids.add(movie.tmdb_id)

        return FeedSection(id="genre_fallback", title="Recommended for You", items=items)

    async def create_feed_item(self, movie: Movie, score: Optional[float], country: str, tmdb: TMDBClient, contributors: List[dict] = None, provider_service: ProviderService = None, streaming_providers: List[str] = None) -> FeedItem:
        """Helper to create a FeedItem from a Movie (DB Object).

        `score` es un coseno película→película SIN alterar, o **None**. Hoy los 13
        productores pasan None: los que pasaban constantes no medían nada, y los dos
        que pasaban un coseno lo entregaban ya penalizado o mezclado con calidad
        (ver models/schemas.py para la medición completa).

        La normalización se queda porque es el contrato: quien algún día pase un
        coseno de verdad obtiene la escala correcta sin tener que descubrir cuál es.
        """
        if streaming_providers is None:
            streaming_providers = []
            if provider_service:
                providers_data = await provider_service.get_providers(movie.id, country)
                streaming_providers = [p["provider_name"] for p in providers_data]
            else:
                providers_data = await tmdb.get_watch_providers(movie.tmdb_id, country)
                if providers_data:
                    for provider_type in ["flatrate", "free"]:
                        if provider_type in providers_data:
                            streaming_providers.extend([p["provider_name"] for p in providers_data[provider_type]])
        
        # Escala película→película: los dos productores que llegan aquí con un
        # coseno real (BYW, hidden gems) consultan con el vector GUARDADO de una
        # película o con un centroide de medoides, y ambos viven en la misma
        # población — 0.776–0.858 medido. Con la escala de consultas el feed del
        # usuario 210 saturaba 48 de 50. Ver utils/scoring.py.
        final_score = None if score is None else normalize_film_similarity_score(score)

        # Country-aware "coming soon" badge, feed-wide: films trending worldwide
        # (popular row, BYW, …) can be unreleased in the user's country. Only
        # asserts on a positive future TMDB date for that country — no date, no
        # badge (the dedicated upcoming section keeps its richer ES/WW ladder
        # and is skipped here via its pre-set upcoming contributor).
        contributors = list(contributors) if contributors else []
        if not any(c.get("type") == "upcoming" for c in contributors):
            raw_local = (movie.release_dates or {}).get(country.upper()) if country else None
            try:
                local_date = date.fromisoformat(raw_local) if raw_local else None
            except (ValueError, TypeError):
                local_date = None
            if local_date and local_date > date.today():
                contributors.append({
                    "type": "upcoming",
                    "label": "Coming soon",
                    "release_badge": f"{country.upper()} · {local_date.strftime('%d %b').upper()}",
                    "release_note": None,
                })

        return FeedItem(
            id=movie.tmdb_id,
            title=movie.title,
            poster_url=movie.poster_path,
            match_score=None if final_score is None else round(final_score, 0),
            streaming_providers=list(set(streaming_providers)),
            year=movie.year,
            runtime=movie.runtime,
            letterboxd_uri=movie.letterboxd_uri,
            rating=movie.vote_average,
            overview=movie.overview,
            contributors=contributors,
            vectorbox_score=movie.vectorbox_score,
            imdb_rating=movie.imdb_rating,
            metacritic_rating=movie.metacritic_rating,

            title_es=movie.title_es,
            overview_es=movie.overview_es,
            release_dates=movie.release_dates,
            backdrop_url=movie.backdrop_path,
        )

    async def get_because_you_watched_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        qdrant: QdrantService,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None,
        background_tasks = None,
        precomputed_anti_vector = None,
        filters: Dict = None,
        pool_limit: int = None,
        include_shorts: bool = False,
    ) -> FeedSection:
        """Signal A: Because you watched [Movie X] — Item-Item Collaborative Filtering"""
        with _tracer.start_as_current_span("trident.signal_a.because_you_watched") as span:
            span.set_attribute("user_id", user_id)
            span.set_attribute("country", country)

            # FIX 6: Pre-order in SQL by (rating DESC NULLS LAST, watched_date DESC NULLS LAST)
            # so the top-N truncation cannot drop a 5.0★ recent watch. Without ordering, an
            # arbitrary heap slice was being returned and high-quality anchors (e.g. Hamnet)
            # never reached the Python re-scoring step. Python still re-scores with the full
            # formula (recency decay + rewatch boost) — SQL just guarantees the best are present.
            # T-03: Prefer movies with healthy embeddings (or unchecked NULL) — corrupt vectors
            # produce nonsensical anchors that drag the whole row off-topic.
            result = await db.execute(
                select(UserRating, Movie)
                .join(Movie, UserRating.movie_id == Movie.id)
                .where(
                    UserRating.user_id == user_id,
                    or_(
                        UserRating.rating >= 3.5,
                        UserRating.is_liked.is_(True)
                    )
                )
                .where(
                    or_(
                        Movie.embedding_quality_score >= 0.25,
                        Movie.embedding_quality_score.is_(None)
                    )
                )
                .order_by(
                    UserRating.rating.desc().nullslast(),
                    UserRating.watched_date.desc().nullslast(),
                )
                .limit(500)
            )

            candidates = result.all()
            if not candidates:
                span.set_attribute("result_count", 0)
                # Imp 9: Cold start fallback
                return await self._get_genre_fallback_section(user_id, db, tmdb, seen_ids, country, provider_service)

            # Sort entirely in Python — _score_anchor_candidate handles rating=None and NULL dates
            now = datetime.now(timezone.utc)
            # Neutro de las filas sin fecha: la mediana del decay de las fechadas de ESTE
            # usuario. Un 0.5 fijo castiga el dato ausente — ver el docstring del scorer.
            _decays = [
                0.5 ** (max(0, (now - (ur.watched_date.replace(tzinfo=timezone.utc)
                                       if ur.watched_date.tzinfo is None else ur.watched_date)).days) / 730)
                for ur, _ in candidates if ur.watched_date is not None
            ]
            neutral_decay = float(np.median(_decays)) if _decays else 0.5
            scored_candidates = []
            for row in candidates:
                user_rating, movie = row
                anchor_score = _score_anchor_candidate(
                    neutral_decay=neutral_decay,
                    rating=user_rating.rating,
                    # NO `or created_at`: `created_at` es la fecha de IMPORT, y tras
                    # una re-subida son todas hoy — 787 filas del user 212 pasaban a
                    # parecer vistas hoy y se comían la selección de anclas
                    # (2026-08-11). El scorer ya trata None como neutro (730 días).
                    watched_date=user_rating.watched_date,
                    now=now,
                    watch_count=getattr(user_rating, 'watch_count', 1) or 1
                )
                scored_candidates.append((anchor_score, user_rating, movie))

            scored_candidates.sort(key=lambda x: x[0], reverse=True)

            # Log the top 3 scored candidates so anchor selection is auditable from prod logs
            top_preview = ", ".join(
                f"{m.title} ({round(s, 3)})"
                for s, _, m in scored_candidates[:3]
            )
            logger.info(f"[Because you watched] Top 3 anchor candidates: {top_preview}")

            # FIX 4: Reuse precomputed anti-vector if available (avoids second Qdrant/DB round-trip)
            anti_vector = precomputed_anti_vector if precomputed_anti_vector is not None else await self._get_anti_vector(user_id, db, qdrant)
            anti_vector_np = np.array(anti_vector) if anti_vector else None

            # N+1 FIX: Batch-fetch vectors for top anchor candidates in a single Qdrant call
            # instead of one get_vector() per iteration. We cap at 10 anchors since Signal A
            # returns on the first anchor that produces sufficient results.
            _top_anchor_tmdb_ids = [m.tmdb_id for _, _, m in scored_candidates[:10]]
            _anchor_vectors_map: Dict[int, list] = await qdrant.get_vectors_batch(_top_anchor_tmdb_ids)

            for _, user_rating, anchor_movie in scored_candidates:
                logger.info(f"[Because you watched] Anchor: {anchor_movie.title} ({anchor_movie.year}) rating={user_rating.rating} watch_count={getattr(user_rating, 'watch_count', 1)}")

                # Prefer the stored Qdrant vector — it was built from cinematic_description
                # (Groq-enriched, no title) and is the canonical encoding for the catalogue.
                # Regenerating on the fly creates a different vector space and produces
                # off-theme neighbours. Only fall back to fresh encoding when the anchor
                # has no stored vector (e.g. just-ingested film).
                # Use the pre-fetched batch map; fall back to a live call only for anchors
                # outside the initial top-10 window (rare — Signal A exits on first hit).
                anchor_vector = _anchor_vectors_map.get(anchor_movie.tmdb_id) or await qdrant.get_vector(anchor_movie.tmdb_id)

                if not anchor_vector and self.embedding_service:
                    text_override = anchor_movie.cinematic_description if anchor_movie.cinematic_description else None
                    if anchor_movie.keywords is None:
                        keywords = await tmdb.get_movie_keywords(anchor_movie.tmdb_id) or []
                    else:
                        keywords = anchor_movie.keywords
                    loop = asyncio.get_running_loop()
                    anchor_vector = await loop.run_in_executor(
                        None,
                        lambda: self.embedding_service.generate_embedding({
                            "title": anchor_movie.title,
                            "overview": anchor_movie.overview or "",
                            "genres": anchor_movie.genres or [],
                            "keywords": keywords,
                        }, text_override=text_override).tolist()
                    )

                if not anchor_vector:
                    continue

                similar_results = await qdrant.search_similar(
                    query_vector=anchor_vector,
                    limit=500,
                    filters=filters,  # F8: rail constraints applied inside the search (no starvation)
                )
                
                found_tmdb_ids = [res["movie_id"] for res in similar_results]
                
                existing_movies_result = await db.execute(
                    select(Movie.tmdb_id).where(Movie.tmdb_id.in_(found_tmdb_ids))
                )
                existing_tmdb_ids = set(existing_movies_result.scalars().all())
                
                missing_ids = [mid for mid in found_tmdb_ids if mid not in existing_tmdb_ids]
                
                if missing_ids:
                    ids_to_ingest = missing_ids[:5]
                    if background_tasks:
                        for mid in ids_to_ingest:
                            background_tasks.add_task(_ingest_movie_background, mid)
                    else:
                        for mid in ids_to_ingest:
                            try:
                                await _ingest_movie_background(mid)
                            except Exception as e:
                                logger.error(f"Failed to auto-ingest movie {mid}: {e}")
                
                target_ids = []
                for res in similar_results:
                    mid = res["movie_id"]
                    if mid not in seen_ids and mid != anchor_movie.tmdb_id:
                        target_ids.append(mid)
                
                target_ids = target_ids[:100]
                
                if target_ids:
                    movies_result = await db.execute(
                        select(Movie)
                        .where(Movie.tmdb_id.in_(target_ids))
                        .where(*movie_quality_gate(include_shorts))
                        .where(Movie.vectorbox_score >= BYW_MIN_VBS)
                    )
                    fetched_movies = movies_result.scalars().all()
                    movie_map = {m.tmdb_id: m for m in fetched_movies}

                    if provider_service:
                        valid_internal_ids = [m.id for m in fetched_movies]
                        providers_map = await provider_service.get_providers_batch(valid_internal_ids, country)
                    else:
                        providers_map = {}
                else:
                    movie_map = {}
                    providers_map = {}

                # Imp 5: Apply anti-vector penalty to similarity scores
                scores_map = {res["movie_id"]: res["score"] for res in similar_results}
                if anti_vector_np is not None:
                    # Batch fetch candidate vectors for anti-vector comparison
                    candidate_tmdb_ids = [tid for tid in target_ids if tid in movie_map]
                    if candidate_tmdb_ids:
                        candidate_vectors = await qdrant.get_vectors_batch(candidate_tmdb_ids)
                        cos_by_id = {}
                        for tid, vec in candidate_vectors.items():
                            vec_np = np.array(vec)
                            norm_product = np.linalg.norm(vec_np) * np.linalg.norm(anti_vector_np)
                            if norm_product > 0:
                                cos_by_id[tid] = float(np.dot(vec_np, anti_vector_np) / norm_product)
                        # Aquí había dos cosenos a pelo, 0.80 (×0.3) y 0.65 (×0.6).
                        # Medidos el 2026-09-01 sobre los vecinos reales de las
                        # anclas de 6 usuarios (84-100 candidatos cada uno): el de
                        # 0.80 **no disparó ni una vez** —máximo observado 0.794— y
                        # el de 0.65 tocaba del 11,0% al 61,9% según el usuario. Un
                        # coseno absoluto no significa lo mismo para dos personas en
                        # este espacio; el decil de la propia lista sí. Ver
                        # `utils.anti_vector.most_anti_similar`.
                        for tid in most_anti_similar(cos_by_id):
                            scores_map[tid] = scores_map.get(tid, 0) * BYW_ANTI_VECTOR_DEMOTE_FACTOR

                # Build intermediate candidate dicts for MMR
                mmr_candidates = []
                for res in similar_results:
                    movie_id = res["movie_id"]
                    if movie_id in seen_ids or movie_id == anchor_movie.tmdb_id:
                        continue
                    
                    movie = movie_map.get(movie_id)
                    if movie:
                        penalized_score = scores_map.get(movie_id, res["score"])
                        mmr_candidates.append({
                            "movie_id": movie.id,  # internal ID for MMR vectors_map
                            "tmdb_id": movie.tmdb_id,
                            "score": penalized_score,
                            "movie": movie,
                        })
                        # NOTE: keep this cap FIXED at 50 — it feeds MMR, and a bigger
                        # MMR input would reshuffle the displayed top-15 vs the live feed
                        # (greedy diversity). The F8 deep tail draws from these same 50.
                        if len(mmr_candidates) >= 50:
                            break

                if not mmr_candidates:
                    continue

                # Genre coherence: drop candidates that share no distinctive genre
                # with the user's rated history. Prevents generic blockbusters from
                # surfacing through anchor proximity alone.
                from utils.genre_utils import get_distinctive_user_genres, GENERIC_GENRES
                distinctive_genres = await get_distinctive_user_genres(user_id, db)
                if distinctive_genres and len(mmr_candidates) >= 5:
                    coherent = []
                    for c in mmr_candidates:
                        m_genres = set(c["movie"].genres or [])
                        # Allow through: no genre metadata, or all genres are generic
                        if not m_genres or not (m_genres - GENERIC_GENRES):
                            coherent.append(c)
                            continue
                        # Allow through: shares at least one distinctive genre
                        if m_genres & distinctive_genres:
                            coherent.append(c)
                            continue
                    if len(coherent) >= 5:
                        logger.info(
                            f"[BYW] Genre coherence: {len(mmr_candidates)} → "
                            f"{len(coherent)} candidates, "
                            f"distinctive={sorted(distinctive_genres)}"
                        )
                        mmr_candidates = coherent

                # Imp 6: Apply MMR reranking
                try:
                    mmr_tmdb_ids = [c["tmdb_id"] for c in mmr_candidates]
                    mmr_vectors_raw = await qdrant.get_vectors_batch(mmr_tmdb_ids)
                    # Map tmdb_id → internal_id for vectors_map
                    tmdb_to_internal = {c["tmdb_id"]: c["movie_id"] for c in mmr_candidates}
                    vectors_map_mmr = {
                        tmdb_to_internal[tid]: np.array(v)
                        for tid, v in mmr_vectors_raw.items()
                        if tid in tmdb_to_internal
                    }
                    
                    loop = asyncio.get_running_loop()
                    # lambda=0.8 (relevance-dominant): "Because you watched X" is a
                    # SIMILARITY row — its whole point is that items resemble the
                    # anchor. The MMR diversity term is kept only to break up
                    # near-duplicates (saga/sequel flooding), which it still does
                    # at 0.2 weight. At lambda=0.5 it was demoting genuinely on-
                    # theme neighbours for being "too similar" and pulling weaker,
                    # off-theme films up to fill the row (e.g. Howl's anchor: 0.85
                    # surfaces NeverEnding Story / Bridge to Terabithia / Tinker
                    # Bell over Dreams / Donkey Skin / Green Knight at 0.5).
                    mmr_func = functools.partial(
                        self.clustering.mmr_rerank,
                        mmr_candidates, vectors_map_mmr, 15, lambda_param=0.8
                    )
                    mmr_results = await loop.run_in_executor(None, mmr_func)
                except Exception as e:
                    logger.error(f"MMR failed in Signal A, falling back to top-15: {e}")
                    mmr_results = mmr_candidates[:15]

                # F8 deep pool (filtered feed only): append the remaining candidates in
                # score order below the untouched MMR top-15 — output-filter fodder only.
                if pool_limit and len(mmr_results) < pool_limit:
                    picked_ids = {c["movie_id"] for c in mmr_results}
                    tail = sorted(
                        (c for c in mmr_candidates if c["movie_id"] not in picked_ids),
                        key=lambda c: c["score"], reverse=True,
                    )
                    mmr_results = list(mmr_results) + tail[: pool_limit - len(mmr_results)]

                items = []
                for cand in mmr_results:
                    movie = cand["movie"]
                    p_data = providers_map.get(movie.id, [])
                    s_providers = [p["provider_name"] for p in p_data]

                    item = await self.create_feed_item(
                        movie, None, country, tmdb,
                        provider_service=provider_service,
                        streaming_providers=s_providers,
                        contributors=[{
                            "type": "anchor",
                            "seed_title": anchor_movie.title,
                            "seed_year": anchor_movie.year,
                            "seed_rating": float(user_rating.rating or 0),
                            "similarity": round(cand["score"], 3)
                        }]
                    )
                    items.append(item)
                    seen_ids.add(movie.tmdb_id)

                if items:
                    span.set_attribute("result_count", len(items))
                    span.set_attribute("anchor_movie", anchor_movie.title)
                    return FeedSection(
                        id="because_you_watched",
                        title=f"Because you watched {anchor_movie.title}",
                        items=items
                    )
                    
            span.set_attribute("result_count", 0)
            # Imp 9: Cold start fallback instead of empty section
            return await self._get_genre_fallback_section(user_id, db, tmdb, seen_ids, country, provider_service)

    async def get_niche_picks_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None,
        filters: Dict = None,
        include_shorts: bool = False,
    ) -> FeedSection:
        """
        Global evocative theme recommendations. Rotates through GLOBAL_THEMES.
        DB-first, genre-strict, quality-gated, with score randomization for variety.
        """
        import redis.asyncio as aioredis
        from config import REDIS_URL, FEED_CACHE_VERSION
        import random

        r = None
        # Random theme on cache miss so users who hit the section right after expiry
        # don't always land on the first theme (Sleep Optional)
        theme_index = random.randint(0, len(GLOBAL_THEMES) - 1)
        try:
            r = aioredis.from_url(REDIS_URL, decode_responses=True)
            rotation_key = f"niche_theme_rotation:{FEED_CACHE_VERSION}:{user_id}"
            raw = await r.get(rotation_key)
            if raw is not None:
                theme_index = (int(raw) + 1) % len(GLOBAL_THEMES)
            await r.setex(rotation_key, 60 * 60 * 24 * 7, str(theme_index))
        except Exception as e:
            logger.warning(f"Redis niche theme rotation failed: {e}")
        finally:
            if r:
                await r.close()

        theme = GLOBAL_THEMES[theme_index]

        excluded_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.is_watched.is_(True), UserRating.is_rejected.is_(True)))
        )
        excluded_ids = set(excluded_result.scalars().all())

        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy import cast, String

        include_array = cast(theme["include_genres"], ARRAY(String))

        def _build_theme_query(min_score: int, min_votes: int):
            q = apply_rail_filters(
                select(Movie)
                # El gate va aquí y no a mano: esta fila se escribía sus propios
                # filtros y por eso se saltaba `is_excluded` y `is_adult` (0 filas
                # hoy, pero curar no es control de acceso — cualquier usuario puede
                # empujar una fila al catálogo compartido al puntuar). Los umbrales
                # del tema son MÁS estrictos que el gate en votos y VBS, así que lo
                # único que cambia de verdad son los 236 cortos que colaba.
                .where(*movie_quality_gate(include_shorts))
                .where(Movie.genres.overlap(include_array))
                .where(Movie.vectorbox_score >= min_score)
                .where(Movie.vote_count >= min_votes)
                .where(Movie.vote_average >= 5.0),
                filters,
            )
            if excluded_ids:
                q = q.where(Movie.id.notin_(excluded_ids))
            if "max_year" in theme:
                q = q.where(Movie.year <= theme["max_year"])
            if "min_runtime" in theme:
                q = q.where(Movie.runtime >= theme["min_runtime"])
            if "original_language_not" in theme:
                q = q.where(Movie.original_language != theme["original_language_not"])
            if "max_popularity" in theme:
                q = q.where(Movie.popularity <= theme["max_popularity"])
            return q

        def _apply_post_filters(rows) -> list:
            exclude_genres = set(theme.get("exclude_genres", []))
            require_any = set(theme.get("require_any", []))
            keep = []
            for movie in rows:
                if movie.tmdb_id in seen_ids:
                    continue
                if exclude_genres and set(movie.genres or []) & exclude_genres:
                    continue
                if require_any and not (set(movie.genres or []) & require_any):
                    continue
                keep.append(movie)
            return keep

        # F-21 dynamic threshold fallback. Fetch the candidate pool ONCE at
        # the floor thresholds (ordered by VBS desc — top films we'd ever
        # consider), then raise the gates in Python until we have ≥ 5
        # survivors or hit the floors. Floors are conservative; below
        # VBS 50 / vote_count 20 we'd be surfacing low-confidence films,
        # which beats showing an empty row but should be rare.
        TARGET_MIN = 5
        SCORE_FLOOR = 50
        VOTES_FLOOR = 20
        SCORE_STEP = 5

        pool_result = await db.execute(
            _build_theme_query(SCORE_FLOOR, VOTES_FLOOR)
            # `_build_theme_query` ya excluye los NULL, pero el invariante se deja
            # visible aqui en vez de a dos saltos de distancia.
            .order_by(Movie.vectorbox_score.desc().nulls_last())
            .limit(200)
        )
        pool = _apply_post_filters(pool_result.scalars().all())

        current_score = theme["min_score"]
        current_votes = theme["min_votes"]
        filtered: list = []
        while True:
            filtered = [
                m for m in pool
                if (m.vectorbox_score or 0) >= current_score
                and (m.vote_count or 0) >= current_votes
            ]
            if len(filtered) >= TARGET_MIN:
                break
            if current_score <= SCORE_FLOOR and current_votes <= VOTES_FLOOR:
                break
            if current_score > SCORE_FLOOR:
                current_score = max(SCORE_FLOOR, current_score - SCORE_STEP)
            else:
                current_votes = max(VOTES_FLOOR, current_votes // 2)
        if current_score != theme["min_score"] or current_votes != theme["min_votes"]:
            logger.info(
                f"[niche_picks] theme={theme['id']!r} relaxed quality gate: "
                f"min_score {theme['min_score']}->{current_score}, "
                f"min_votes {theme['min_votes']}->{current_votes} "
                f"(needed ≥{TARGET_MIN}, got {len(filtered)})"
            )

        if not filtered:
            return FeedSection(id="niche_picks", title=theme["title"], items=[])

        # Prior de década. Esta fila es género + calidad y NO mira al usuario, así que
        # ordenar por VBS devuelve el canon de ese género — y el cine antiguo del catálogo
        # está filtrado por supervivencia (VBS medio 66.0 pre-1975 contra 57.2 del moderno).
        # Resultado medido el 2026-08-11 en `Comfort Watch` de user 212: **45% anterior a
        # 1975** cuando su biblioteca es el **1%**. Chaplin, Keaton y Wilder para alguien
        # cuyo gusto empieza en los 2000.
        #
        # peso = (cuota del usuario en esa década + ALFA) / (1 + ALFA). Nunca elimina, baja.
        # ALFA=4.0 medido contra cuatro perfiles de época construidos a propósito: corrige
        # al de gusto moderno (40%→5% pre-75) y **le da MÁS clásico al que ama el clásico**
        # (40%→80%). La coherencia con la biblioteca sube en los cuatro. Con ALFA≤1 el prior
        # domina y borra el cine antiguo del todo, que es cambiar un extremo por otro.
        decadas = await db.execute(
            select(Movie.year)
            .join(UserRating, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.rating >= 4.0, UserRating.is_liked.is_(True)))
            .where(Movie.year.isnot(None))
        )
        años = decadas.scalars().all()
        cuota: dict[int, float] = {}
        if años:
            cont = Counter((y // 10) * 10 for y in años)
            cuota = {d: c / len(años) for d, c in cont.items()}

        def _peso_decada(movie) -> float:
            if not movie.year or not cuota:
                return 1.0
            return (cuota.get((movie.year // 10) * 10, 0.0) + NICHE_ERA_ALPHA) / (1 + NICHE_ERA_ALPHA)

        scored = [
            (movie, (movie.vectorbox_score or 0) * _peso_decada(movie) * random.uniform(0.7, 1.3))
            for movie in filtered
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        selected = [movie for movie, _ in scored[:20]]

        providers_map = {}
        if provider_service:
            provider_ids = [m.id for m in selected]
            providers_map = await provider_service.get_providers_batch(provider_ids, country)

        items = []
        for movie in selected:
            if movie.tmdb_id in seen_ids:
                continue
            movie_providers = providers_map.get(movie.id, [])
            flat_providers = [p["provider_name"] for p in movie_providers]
            item = await self.create_feed_item(
                movie, None, country, tmdb,
                streaming_providers=flat_providers,
                contributors=[{
                    "type": "cluster",
                    "cluster_name": theme["title"],
                    "label": "Curated thematic pick"
                }]
            )
            seen_ids.add(movie.tmdb_id)
            items.append(item)

        return FeedSection(
            id="niche_picks",
            title=theme["title"],
            items=items
        )


    async def get_hidden_gems_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None,
        filters: Dict = None,
        pool_limit: int = None,
        include_shorts: bool = False,
    ) -> FeedSection:
        """Signal C: Hidden Gems — Score-to-Hype Filtering"""
        with _tracer.start_as_current_span("trident.signal_c.hidden_gems") as span:
            span.set_attribute("user_id", user_id)
            span.set_attribute("country", country)
            # Step 1: Get dynamic thresholds based on user's movie count
            user_count_result = await db.execute(
                select(func.count(UserRating.id))
                .where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
            )
            user_movie_count = user_count_result.scalar() or 0
            thresholds = _get_signal_c_thresholds(user_movie_count)
            logger.info(f"[Signal C] User {user_id} has {user_movie_count} movies, using thresholds: {thresholds}")
            
            # Step 2: DB Query — Fetch high-quality unseen candidates.
            # Exclude both watched and rejected films (internal IDs).
            excluded_result = await db.execute(
                select(UserRating.movie_id)
                .where(UserRating.user_id == user_id)
                .where(or_(UserRating.is_watched.is_(True), UserRating.is_rejected.is_(True)))
            )
            excluded_internal_ids = set(excluded_result.scalars().all())

            # Step 2a: the user's taste center, needed BEFORE the candidate query
            # so the pool can be ordered by affinity instead of by score. Mean of
            # their cluster medoids — medoids are real films, so this centre is
            # far less hub-prone than the mean of a whole library.
            global_center = await self._user_center_vector(user_id, db)

            # Affinity-ordered pool. The old pool was `ORDER BY vectorbox_score
            # DESC LIMIT 200` over ~1.4-3.8k eligible films: the top 200 by score
            # are almost the same 200 for everyone, so three different users
            # shared 2.7 of their 10 gems and the row read as a canon list.
            # Ordering the pool by proximity to the user's centre instead drops
            # that overlap to 1.3/10 (measured 2026-08-04, MMR included) at a
            # cost of ~3 VBS points. Quality still decides the RANKING (0.7/0.3);
            # it just no longer decides who is allowed in the room.
            affinity_ids: List[int] = []
            if global_center is not None:
                try:
                    hits = await self.qdrant.search_similar(
                        query_vector=(global_center.tolist() if hasattr(global_center, "tolist")
                                      else list(global_center)),
                        limit=SIGNAL_C_POOL,
                        filters={
                            "min_vectorbox_score": thresholds["min_score"],
                            "min_vote_count": thresholds["min_votes"],
                            **({"exclude_tmdb_ids": list(seen_ids)} if seen_ids else {}),
                        },
                    )
                    affinity_ids = [int((h.get("metadata") or {}).get("tmdb_id") or h["movie_id"])
                                    for h in hits]
                except Exception as e:
                    logger.warning(f"[Signal C] affinity pool failed, falling back to score order: {e}")

            # F8: rail constraints applied in-query (wide 200-pool, no starvation).
            _flt = _movie_filter_clauses(filters)

            result = await db.execute(
                select(Movie)
                .where(*movie_quality_gate(include_shorts))
                # Enriched-vector gate: hidden gems is a "trust us" quality claim —
                # never surface films whose vector/description is still legacy-recipe
                .where(Movie.has_enriched_embedding.is_(True))
                .where(Movie.vectorbox_score >= thresholds["min_score"])
                # Fame ceiling on IMDb votes, NOT the old `popularity <= 30-40`.
                # TMDB popularity decays with time: only 150 of 20.339 catalogue
                # films exceed 30 and the p99 is 25.5, so that cap excluded ~0.7%
                # and let Rear Window (568k votes, popularity 11) sit in a row
                # called Hidden Gems. Vote count is what "how known is this"
                # actually means. Measured: median of the row drops from ~137-196k
                # votes to ~35-54k, VBS 94.9 -> ~90.
                .where(Movie.imdb_vote_count <= SIGNAL_C_MAX_VOTES)
                .where(Movie.vote_count >= thresholds["min_votes"])
                .where(Movie.id.notin_(excluded_internal_ids) if excluded_internal_ids else True)
                .where(*_flt)
                .where(Movie.tmdb_id.in_(affinity_ids) if affinity_ids else True)
                .order_by(desc(Movie.vectorbox_score))
                .limit(200 if not affinity_ids else SIGNAL_C_POOL)
            )
            candidates = result.scalars().all()
            if affinity_ids:
                # Keep the AFFINITY order, not the score order — sorting the pool
                # by score again would put the same canon back at the front and
                # undo the whole change (measured: overlap stayed at 2.3/10
                # instead of dropping to 1.3 until this line was fixed).
                rank_of = {tid: i for i, tid in enumerate(affinity_ids)}
                candidates = sorted(candidates, key=lambda m: rank_of.get(m.tmdb_id, 10**6))[:200]
                # Post-filter survival: the vote ceiling and the rail filters are
                # applied AFTER the affinity pool, so log what got through. A pool
                # that starves here silently is the failure this note exists for.
                logger.info(f"[Signal C] affinity pool {len(affinity_ids)} -> {len(candidates)} "
                            f"survived the vote ceiling and rail filters")
                if len(candidates) < 20:
                    logger.warning("[Signal C] affinity pool starved; widening to score order")
                    candidates = (await db.execute(
                        select(Movie)
                        .where(*movie_quality_gate(include_shorts))
                        .where(Movie.has_enriched_embedding.is_(True))
                        .where(Movie.vectorbox_score >= thresholds["min_score"])
                        .where(Movie.imdb_vote_count <= SIGNAL_C_MAX_VOTES)
                        .where(Movie.vote_count >= thresholds["min_votes"])
                        .where(Movie.id.notin_(excluded_internal_ids) if excluded_internal_ids else True)
                        .where(*_flt)
                        .order_by(desc(Movie.vectorbox_score))
                        .limit(200)
                    )).scalars().all()
            
            if not candidates:
                span.set_attribute("result_count", 0)
                return FeedSection(id="hidden_gems", title="Hidden Gems", items=[])

            # Step 3 moved up to 2a — the centre now also orders the pool.

            # Step 4: Fetch candidate vectors in batch and score
            candidate_tmdb_ids = [m.tmdb_id for m in candidates]
            candidate_vectors_map = await self.qdrant.get_vectors_batch(candidate_tmdb_ids)
            
            mmr_candidates = []
            for movie in candidates:
                # Base score from VectorBox quality (scaled 0-1)
                quality_score = (movie.vectorbox_score / 100.0)
                
                # Similarity signal (if available) - default 0.5 to not penalize
                similarity_score = 0.5
                if global_center is not None:
                    vec = candidate_vectors_map.get(movie.tmdb_id)
                    if vec is not None:
                        vec_np = np.array(vec)
                        # Cosine similarity (assuming normalized vectors)
                        similarity_score = float(np.dot(vec_np, global_center) / (np.linalg.norm(vec_np) * np.linalg.norm(global_center)))
                
                # Combine Score: 70% Quality + 30% Profile Similarity
                combined_score = (quality_score * 0.7) + (similarity_score * 0.3)
                
                # Apply Exoticism Boost (implemented helper)
                boosted_score = _apply_exoticism_boost(combined_score, movie.original_language)
                
                mmr_candidates.append({
                    "movie_id": movie.id,
                    "tmdb_id": movie.tmdb_id,
                    "score": boosted_score,
                    "movie": movie
                })
            
            # Re-sort by boosted score
            mmr_candidates.sort(key=lambda x: x["score"], reverse=True)
            mmr_candidates = mmr_candidates[:50] # Limit for MMR pool

            # Step 5: Apply MMR Diversity — reuse candidate_vectors_map from Step 4 (FIX 7)
            try:
                vectors_map_mmr = {
                    c["movie_id"]: np.array(candidate_vectors_map[c["tmdb_id"]])
                    for c in mmr_candidates if c["tmdb_id"] in candidate_vectors_map
                }
                
                loop = asyncio.get_running_loop()
                mmr_func = functools.partial(
                    self.clustering.mmr_rerank,
                    mmr_candidates, vectors_map_mmr, 10, lambda_param=0.8 # Less diversity, focus on the top candidates
                )
                mmr_results = await loop.run_in_executor(None, mmr_func)
            except Exception as e:
                logger.error(f"MMR failed in Hidden Gems: {e}")
                mmr_results = mmr_candidates[:10]

            # F8 deep pool (filtered feed only): append remaining candidates in score
            # order below the untouched MMR top-10 — output-filter fodder only.
            if pool_limit and len(mmr_results) < pool_limit:
                picked_ids = {c["movie_id"] for c in mmr_results}
                mmr_results = list(mmr_results) + [
                    c for c in mmr_candidates if c["movie_id"] not in picked_ids
                ][: pool_limit - len(mmr_results)]

            if provider_service:
                cand_ids = [c["movie_id"] for c in mmr_results]
                providers_map = await provider_service.get_providers_batch(cand_ids, country)
            else:
                providers_map = {}

            items = []
            for cand in mmr_results:
                movie = cand["movie"]
                p_data = providers_map.get(movie.id, [])
                s_providers = [p["provider_name"] for p in p_data]
                
                item = await self.create_feed_item(
                    movie, None, country, tmdb,
                    provider_service=provider_service,
                    streaming_providers=s_providers,
                    contributors=[{"type": "crowd", "label": "Critically acclaimed, under the radar"}]
                )
                items.append(item)
                seen_ids.add(item.id)
            
            span.set_attribute("result_count", len(items))
            return FeedSection(
                id="hidden_gems",
                title="Hidden Gems",
                items=items
            )

    async def get_available_now_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        streaming_providers: List[int]
    ) -> FeedSection:
        """Available on Your Services"""
        items = []
        orden: List[float] = []   # calidad por item, en paralelo a `items`
        watchlist_result = await db.execute(
            select(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                UserRating.is_watchlist.is_(True),
                UserRating.is_watched.is_(False)
            )
            .limit(200)
        )
        watchlist_movies = watchlist_result.scalars().all()
        
        if streaming_providers:
            provider_service = ProviderService(db, tmdb)
            movie_ids = [m.id for m in watchlist_movies if m.tmdb_id not in seen_ids]
            providers_map = await provider_service.get_providers_batch(movie_ids, country)
            
            for movie in watchlist_movies:
                if movie.tmdb_id in seen_ids:
                    continue
                available_providers = []
                movie_providers = providers_map.get(movie.id, [])
                for p in movie_providers:
                    if p["provider_id"] in streaming_providers:
                        available_providers.append(p["provider_name"])
                
                if available_providers:
                    vb = movie.vectorbox_score
                    va = movie.vote_average
                    # Skip movies with no real score data — no inflated defaults
                    if not vb and not va:
                        continue
                    # Esta fila ordena por CALIDAD, que es lo único que mide: lo
                    # mejor valorado de tu watchlist que está en tus plataformas.
                    # El número vivía en `match_score` — un campo de similitud — y
                    # esto era el ÚNICO sitio del backend que lo leía. Ahora es una
                    # variable local con su nombre, y el campo va a None como en el
                    # resto de filas que no comparan con nada.
                    if vb and vb > 0:
                        calidad = min(99, vb)
                    else:
                        calidad = 55 + min(30, max(0, (va - 5.0) * 7.5))

                    orden.append(calidad)
                    items.append(FeedItem(
                        id=movie.tmdb_id,
                        title=movie.title,
                        poster_url=movie.poster_path,
                        match_score=None,
                        streaming_providers=list(set(available_providers)),
                        year=movie.year,
                        runtime=movie.runtime,
                        letterboxd_uri=movie.letterboxd_uri,
                        rating=va,
                        vectorbox_score=vb,
                        imdb_rating=movie.imdb_rating,
                        metacritic_rating=movie.metacritic_rating,
                        backdrop_url=movie.backdrop_path,

                        title_es=movie.title_es,
                        overview_es=movie.overview_es,
                        overview=movie.overview,
                        contributors=[{
                            "type": "watchlist",
                            "label": "In your watchlist",
                        }],
                    ))
                    seen_ids.add(movie.tmdb_id)

        items = [it for _, it in sorted(zip(orden, items), key=lambda p: -p[0])][:20]

        return FeedSection(
            id="available_now",
            title="Available on Your Services",
            items=items
        )

    async def get_wildcard_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None,
        filters: Dict = None,
        include_shorts: bool = False,
    ) -> Optional[FeedSection]:
        """Wildcard Section: Outside Your Comfort Zone.

        Excludes the user's top-3 most-loved genres (rating-weighted). The old
        path took the OR-union of dominant_genres across the 3 biggest clusters,
        which routinely covered 6-8 genres and over-restricted the pool (user 212
        was down to 161 candidates; with top-3 the pool is 542 — 3.4×).
        """
        from services.clustering_service import ClusteringService
        prefs = await ClusteringService.get_user_genre_preferences(user_id, db)
        excluded_genres = {g for g, _ in prefs[:3]}

        if not excluded_genres:
            return None

        # FIX 5: Push genre exclusion and seen filter to DB; use func.random() to avoid 1000-row scan
        excluded_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.is_watched.is_(True), UserRating.is_rejected.is_(True)))
        )
        excluded_internal_ids = set(excluded_result.scalars().all())

        excluded_array = list(excluded_genres)
        q = apply_rail_filters(
            select(Movie)
            .where(*movie_quality_gate(include_shorts))
            .where(Movie.vectorbox_score >= 45)
            .where(Movie.vote_average > 7.0)
            .where(Movie.vote_count > 100)
            .where(~Movie.genres.overlap(excluded_array)),
            filters,
        )
        if excluded_internal_ids:
            q = q.where(Movie.id.notin_(excluded_internal_ids))
        q = q.order_by(func.random()).limit(50)
        result = await db.execute(q)
        wildcard_candidates = [m for m in result.scalars().all() if m.tmdb_id not in seen_ids]

        if not wildcard_candidates:
            return None

        sample = wildcard_candidates[:10]
        
        if provider_service:
            sample_ids = [m.id for m in sample]
            providers_map = await provider_service.get_providers_batch(sample_ids, country)
        else:
            providers_map = {}

        items = []
        for m in sample:
            movie_providers = providers_map.get(m.id, [])
            flat_providers = [p["provider_name"] for p in movie_providers]
            item = await self.create_feed_item(m, None, country, tmdb, streaming_providers=flat_providers,
                contributors=[{"type": "vibe", "label": "Outside your comfort zone"}])
            items.append(item)
            seen_ids.add(m.tmdb_id)

        return FeedSection(
            id="wildcard",
            title="Outside Your Comfort Zone",
            items=items
        )

    async def get_random_recommendations_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None,
        filters: Dict = None,
        include_shorts: bool = False,
    ) -> Optional[FeedSection]:
        """Random Picks"""
        # FIX 5: Push seen filter to DB and use func.random() — avoids 500-row scan
        excluded_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.is_watched.is_(True), UserRating.is_rejected.is_(True)))
        )
        excluded_internal_ids = set(excluded_result.scalars().all())

        # Fetches 30 to show ~12 — the thinnest margin of any metadata row, and
        # it showed: 2.2 of 12 survived a min_vectorbox_score=80 post-filter.
        q = apply_rail_filters(
            select(Movie)
            .where(*movie_quality_gate(include_shorts))
            .where(Movie.vectorbox_score.between(1, 99)),
            filters,
        )
        if excluded_internal_ids:
            q = q.where(Movie.id.notin_(excluded_internal_ids))
        q = q.order_by(func.random()).limit(30)
        result = await db.execute(q)
        candidates = result.scalars().all()

        unseen_candidates = [m for m in candidates if m.tmdb_id not in seen_ids]

        if not unseen_candidates:
            return None

        sample = unseen_candidates[:10]
        
        if provider_service:
            sample_ids = [m.id for m in sample]
            providers_map = await provider_service.get_providers_batch(sample_ids, country)
        else:
            providers_map = {}
        
        items = []
        for m in sample:
            movie_providers = providers_map.get(m.id, [])
            flat_providers = [p["provider_name"] for p in movie_providers]
            item = await self.create_feed_item(m, None, country, tmdb, streaming_providers=flat_providers,
                contributors=[{"type": "crowd", "label": "High quality discovery"}])
            items.append(item)
            seen_ids.add(m.tmdb_id)

        return FeedSection(
            id="random_picks", 
            # "Top" no: la fila tira de todo el catálogo sobre la puerta de calidad
            # (VBS 1-99 medido: media 61, mínimo 46). Prometer "top" y servir el
            # montón entero desgasta la marca a cambio de una palabra.
            title="Random Picks",
            items=items
        )

    async def get_popular_on_letterboxd_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        country: str,
        provider_service: ProviderService = None,
        include_shorts: bool = False,
    ) -> Optional[FeedSection]:
        """Popular on Letterboxd"""
        trending_service = TrendingService(db)
        try:
            popular_items = await trending_service.get_popular_movie_items()
        finally:
            await trending_service.close()

        if not popular_items:
            return None

        # Soft curation: drop films with an explicitly-low Letterboxd rating
        # (< 2.5). When `letterboxd_rating` is None (legacy cache shape — the
        # Trakt fallback that also produced None was deleted 2026-08-18) we keep
        # the film: absent is not low.
        LB_RATING_FLOOR = 2.5
        filtered_items = [
            it for it in popular_items
            if it.get("letterboxd_rating") is None or it["letterboxd_rating"] >= LB_RATING_FLOOR
        ]
        popular_ids = [it["tmdb_id"] for it in filtered_items]
        # The scraped Popular-Chart rating lives on the cache item, NOT the
        # Movie.letterboxd_rating column (nothing writes that column). Surface
        # it from here; None (Trakt fallback) → card shows the year instead.
        rating_by_tmdb = {it["tmdb_id"]: it.get("letterboxd_rating") for it in filtered_items}

        if not popular_ids:
            return None

        result = await db.execute(
            select(Movie)
            .where(Movie.tmdb_id.in_(popular_ids))
            .where(*movie_quality_gate(include_shorts))
        )
        fetched_movies = result.scalars().all()
        movies_map = {m.tmdb_id: m for m in fetched_movies}

        # FIX 8: Filter out movies the user has already watched OR rejected
        excluded_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.is_watched.is_(True), UserRating.is_rejected.is_(True)))
        )
        excluded_internal_ids = set(excluded_result.scalars().all())
        # Map internal IDs → tmdb_ids for comparison
        if excluded_internal_ids:
            excluded_tmdb_result = await db.execute(
                select(Movie.tmdb_id).where(Movie.id.in_(excluded_internal_ids))
            )
            excluded_tmdb_ids = set(excluded_tmdb_result.scalars().all())
        else:
            excluded_tmdb_ids = set()

        # Batch-fetch providers (no N+1)
        if provider_service and fetched_movies:
            internal_ids = [m.id for m in fetched_movies]
            providers_map = await provider_service.get_providers_batch(internal_ids, country)
        else:
            providers_map = {}
        
        items = []
        for tmdb_id in popular_ids:
            if tmdb_id in excluded_tmdb_ids:
                continue
            movie = movies_map.get(tmdb_id)
            if movie:
                p_data = providers_map.get(movie.id, [])
                flat_providers = [p["provider_name"] for p in p_data]
                item = await self.create_feed_item(
                    movie, None, country, tmdb,
                    streaming_providers=flat_providers
                )
                scraped_lb = rating_by_tmdb.get(tmdb_id)
                if scraped_lb:
                    item.letterboxd_rating = scraped_lb
                items.append(item)
                
        if not items:
            return None
            
        return FeedSection(
            id="popular_letterboxd",
            title="Popular on Letterboxd This Week",
            items=items
        )

    async def _user_center_vector(self, user_id: int, db: AsyncSession):
        """Mean of the user's cluster medoids — their taste centre.

        Medoids are real films, so this centre sits far from the catalogue mean
        and does not drag the neighbourhood into hub territory the way a mean of
        a whole library does. Returns None for a user with no clusters yet.
        """
        clusters = (await db.execute(
            select(UserCluster).where(UserCluster.user_id == user_id)
        )).scalars().all()
        medoid_ids = [c.medoid_movie_id for c in clusters if c.medoid_movie_id]
        if not medoid_ids:
            return None
        medoid_tmdb_ids = (await db.execute(
            select(Movie.tmdb_id).where(Movie.id.in_(medoid_ids))
        )).scalars().all()
        vectors_map = await self.qdrant.get_vectors_batch(medoid_tmdb_ids)
        if not vectors_map:
            return None
        return np.mean([np.asarray(v) for v in vectors_map.values() if v is not None], axis=0)

    async def get_leaving_soon_section(
        self,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None,
        filters: Dict = None,
        include_shorts: bool = False,
    ) -> Optional[FeedSection]:
        """Lo que se va del streaming en los próximos días.

        Es la única fila del feed con caducidad real: el resto recomienda algo que
        seguirá ahí mañana, ésta dice "te quedan cuatro días".

        **Sólo aparece si el usuario tiene servicios elegidos**, y no es una preferencia
        de interfaz: es la única forma de que la fila no mienta. Medido el 2026-08-17,
        de las 21 películas que se iban, **CERO estaban en un solo servicio** — 11 en
        dos, y Space Jam en SIETE. Sin saber qué tienes contratado, "se va" significa
        "se va de uno de los varios sitios donde vive", que no es urgente para nadie.
        Con tu selección sí se puede responder: se va **de los tuyos** y no te queda en
        ningún otro de los tuyos. Que siga en Prime es irrelevante si no tienes Prime.

        **Ordenar sólo por fecha no valía**, y se vio al mirar la salida: de 12 huecos
        sólo 3 eran urgentes de verdad (hoy, mañana, 2 días) y los otros 9 se iban en
        películas a 18-28 días, dejando fuera del corte a Riders of Justice (VBS 81) y
        Elena (75) que se iban en la misma ventana. La urgencia manda mientras es
        urgente; pasada una semana, "se va" es sólo una etiqueta y decide la calidad.
        De ahí cabeza por FECHA (≤ URGENTE_DIAS) y cola por VBS — el mismo reparto
        cabeza/cola que usa `get_upcoming_section`, allí con afinidad. Si la cola sale
        genérica, el siguiente paso es afinidad como allí; VBS es lo que ya está
        cargado y no cuesta una lectura más.

        Los datos los ingiere la fase 11 del orquestador desde MovieOfTheNight. Su
        ventana es corta: medido el 2026-08-17, con la ingesta parada cinco días las
        candidatas con calidad habían caído de 41 a 3 — no se rompió nada, es que
        habían caducado. Si la fase 11 no corre a diario, esta fila se apaga sola,
        que es el comportamiento correcto: mejor sin fila que con fechas mentirosas.
        """
        from models.database import StreamingChange
        from services.streaming_availability_client import nombre_servicio, SERVICE_TO_TMDB_PROVIDER
        # Diferido: `recommendation_service` importa de este módulo, así que arriba
        # sería circular. El umbral vive allí y sólo allí (CLAUDE.md).
        from services.recommendation_service import MIN_QUALITY_SCORE

        pedidos = set((filters or {}).get("provider_ids") or [])
        if not pedidos:
            return None

        ahora = datetime.now(timezone.utc)
        proximas = (
            select(Movie, StreamingChange.service, StreamingChange.effective_at,
                   MovieAvailability.providers)
            .join(StreamingChange, StreamingChange.tmdb_id == Movie.tmdb_id)
            .outerjoin(MovieAvailability,
                       (MovieAvailability.movie_id == Movie.id)
                       & (MovieAvailability.country_code == (country or "ES").upper()))
            .where(StreamingChange.change_type == "expiring")
            .where(StreamingChange.country_code == (country or "ES").lower())
            .where(StreamingChange.effective_at > ahora)
            .where(*movie_quality_gate(include_shorts))
            .where(Movie.vectorbox_score >= MIN_QUALITY_SCORE)
            .where(Movie.tmdb_id.notin_(seen_ids) if seen_ids else True)
            .order_by(StreamingChange.effective_at.asc())
        )
        # Se va de UNO DE LOS TUYOS: mirar de qué servicio sale, no en cuál está.
        # Si eliges un servicio del que no tenemos datos (Movistar), la lista sale
        # vacía y la fila se oculta — es la respuesta honesta, no un fallo.
        proximas = proximas.where(StreamingChange.service.in_(
            [s for s, pid in SERVICE_TO_TMDB_PROVIDER.items() if pid in pedidos]
        ))
        # `provider_ids` NO se le pasa a apply_rail_filters aquí. Dos motivos, y el
        # segundo rompía: (1) sobra — el criterio de esta fila no es "está en tus
        # servicios" sino "se va de uno tuyo y no te queda en otro", que se resuelve
        # abajo; (2) su EXISTS va contra `movie_availability`, que esta consulta ya
        # une, y SQLAlchemy auto-correlaciona la subconsulta contra la unión externa
        # dejándola sin FROM ("returned no FROM clauses due to auto-correlation").
        otros_filtros = {k: v for k, v in (filters or {}).items() if k != "provider_ids"}
        filas = (await db.execute(apply_rail_filters(proximas, otros_filtros or None))).all()

        # Una película puede salir de dos servicios el mismo mes: se queda la fecha
        # más próxima, que es la que aprieta.
        vistas: Set[int] = set()
        unicas = []
        for movie, service, effective_at, provs in filas:
            if movie.tmdb_id in vistas:
                continue
            # ...y si TE queda en otro servicio TUYO, no se va: se muda. `provs` es la
            # disponibilidad actual, o sea que incluye el que abandona — se descuenta.
            que_deja = SERVICE_TO_TMDB_PROVIDER.get(service)
            en_tmdb = {p.get("provider_id") for p in (provs or [])}
            # El catálogo `prime` de MovieOfTheNight INCLUYE los Amazon Channels (AMC,
            # MGM, Lionsgate...), que son suscripciones de pago aparte dentro de Prime.
            # TMDB los cuenta como proveedores distintos, y tiene razón: con Prime a
            # secas no puedes verlas. Sin este cruce, "se va de Prime" se anunciaba
            # sobre películas que el usuario no podía ver — medido 2026-08-19 con
            # Frantic y Payback, que MotN daba como Prime y sólo están en AMC Channels
            # y Tivify. Se exige que TMDB confirme el servicio que la abandona.
            if que_deja not in en_tmdb:
                continue
            # ...y si TE queda en otro servicio TUYO, no se va: se muda.
            if (en_tmdb & pedidos) - {que_deja}:
                continue
            vistas.add(movie.tmdb_id)
            unicas.append((movie, service, effective_at))

        URGENTE_DIAS = 7
        limite = ahora.date() + timedelta(days=URGENTE_DIAS)
        urgentes = [c for c in unicas if c[2].date() <= limite]
        resto = sorted((c for c in unicas if c[2].date() > limite),
                       key=lambda c: c[0].vectorbox_score or 0, reverse=True)
        candidatas = (urgentes + resto)[:12]

        if not candidatas:
            return None

        providers_map = {}
        if provider_service:
            providers_map = await provider_service.get_providers_batch(
                [m.id for m, _, _ in candidatas], country
            )

        items = []
        for movie, service, effective_at in candidatas:
            dias = (effective_at.date() - ahora.date()).days
            cuando = "Leaves today" if dias <= 0 else ("Leaves tomorrow" if dias == 1 else f"{dias} days left")
            items.append(await self.create_feed_item(
                movie, None, country, tmdb,
                contributors=[{
                    "type": "leaving_soon",
                    "label": f"Leaving {nombre_servicio(service)}",
                    # La chapa de la carátula lleva SERVICIO y días juntos: sin el
                    # servicio la fila no dice de dónde se va (que es lo único que la
                    # hace accionable) y sin los días no se distingue lo de mañana de
                    # lo de dentro de tres semanas, porque la fila mezcla ambos.
                    "release_badge": f"{nombre_servicio(service).upper()} · "
                                     + ("TODAY" if dias <= 0 else f"{dias}D"),
                    "release_note": f"{cuando} · {effective_at.strftime('%d %b')}",
                }],
                streaming_providers=[p["provider_name"] for p in providers_map.get(movie.id, [])],
            ))

        return FeedSection(id="leaving_soon", title="Leaving Your Services", items=items)

    async def get_upcoming_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None,
    ) -> FeedSection:
        """
        Upcoming movies personalized to user taste.
        Shows is_upcoming=True movies filtered by user's dominant genres from clusters.
        """
        from datetime import date
        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy import cast, String
        today = date.today()

        # Top-5 most-loved genres (rating-weighted). Wider net than niche_picks /
        # wildcard because upcoming is mainly mainstream + we want enough films
        # in the pool — the old path of UNION-of-all-clusters yielded 11 genres
        # for user 212 (essentially no filter), while top-3 was too tight on
        # user 210 (cut Star Wars, Avatar etc. from the upcoming pool).
        from services.clustering_service import ClusteringService
        prefs = await ClusteringService.get_user_genre_preferences(user_id, db)
        user_genres = [g for g, _ in prefs[:5]]

        query = (
            select(Movie)
            .where(Movie.is_upcoming.is_(True))
            .where(Movie.tmdb_id.notin_(seen_ids))
            .where(Movie.year.isnot(None))
            # `popularity > 5.0` used to be here. It cut the pool from ~150 to
            # ~40 of the 186 upcoming films and changed nothing on its own —
            # ordering by popularity kept the same top 20 either way (measured).
            # Dropping it is what gives the affinity tail something to pick from.
        )

        if user_genres:
            genre_array = cast(user_genres, ARRAY(String))
            query = query.where(Movie.genres.overlap(genre_array))

        candidates = (await db.execute(query.order_by(desc(Movie.popularity)))).scalars().all()

        # Drop the ones that already came out. `is_upcoming` is cleared by
        # phase 1 of the orchestrator (mark_released_upcoming); when that has not
        # run in a while the flag rots — measured 2026-08-04: 13 of 186 flagged
        # films were already released, the newest metadata refresh was 9 days old
        # and the oldest 5 weeks.
        def _release(m):
            return m.release_date_es or m.release_date_ww
        candidates = [m for m in candidates if not (_release(m) and _release(m) < today)]

        # A radar is two things at once: what is COMING (news, even if it isn't
        # for you) and what is coming FOR YOU. The head used to be the 3 highest
        # TMDB popularity, which is neither: that metric decays daily and ours
        # was 5 weeks stale, one outlier sat 8x above the rest (762.7 vs 95.1),
        # and the three winners had NO announced release date while Spider-Man,
        # out in days, ranked fourth. Sort the head by how SOON it lands — the
        # only thing that puts a film on a radar — and rank the rest by affinity.
        # Undated films (126 of 186) can't be imminent, so they wait in the tail.
        # ...y "más próxima" tiene que significar más próxima AQUÍ. Medido 2026-08-11:
        # ordenando por `_release` (que cae a la fecha mundial) los dos usuarios reales
        # veían la MISMA cabecera — OK! Madam: Bon Voyage, Batwara 1947, Vishwanath &
        # Sons: estrenos regionales indios. El filtro de géneros no los para porque van
        # etiquetados con géneros amplios que solapan con el gusto de cualquiera.
        # De 199 upcoming, 37 tienen fecha española: de sobra para 3 huecos.
        dated = sorted([m for m in candidates if _release(m)], key=_release)
        con_fecha_local = sorted([m for m in candidates if m.release_date_es], key=lambda m: m.release_date_es)
        headlines = con_fecha_local[:RADAR_HEADLINES]
        if len(headlines) < RADAR_HEADLINES:
            # Sin suficientes estrenos locales, se rellena con el orden mundial de antes
            # en vez de dejar huecos: una cabecera corta es peor que una imperfecta.
            ya = {m.tmdb_id for m in headlines}
            headlines += [m for m in dated if m.tmdb_id not in ya][: RADAR_HEADLINES - len(headlines)]
        headline_ids = {m.tmdb_id for m in headlines}
        tail = [m for m in candidates if m.tmdb_id not in headline_ids]
        if tail:
            center = await self._user_center_vector(user_id, db)
            if center is not None:
                vecs = await self.qdrant.get_vectors_batch([m.tmdb_id for m in tail])
                if vecs:
                    cn = center / (np.linalg.norm(center) or 1.0)

                    def affinity(movie):
                        v = vecs.get(movie.tmdb_id)
                        if v is None:
                            return -1.0
                        v = np.asarray(v, dtype=np.float32)
                        return float(np.dot(v, cn) / (np.linalg.norm(v) or 1.0))

                    tail = sorted(tail, key=affinity, reverse=True)
        candidates = (headlines + tail)[:20]

        if provider_service and candidates:
            movie_ids = [m.id for m in candidates]
            providers_map = await provider_service.get_providers_batch(movie_ids, country)
        else:
            providers_map = {}

        items = []
        for movie in candidates:
            if movie.tmdb_id in seen_ids:
                continue

            es_date = movie.release_date_es
            us_date = movie.release_date_us

            release_badge = None
            release_note = None

            if es_date and es_date > today:
                release_badge = f"ES · {es_date.strftime('%d %b').upper()}"
                if us_date and us_date <= today:
                    release_note = "Available now in original version"
            elif us_date and us_date > today:
                release_badge = f"WW · {us_date.strftime('%d %b').upper()}"
            elif us_date and us_date <= today and not es_date:
                release_badge = "OUT · NO ES DATE"
                release_note = "Available in original · Not confirmed for ES"
            else:
                release_badge = "COMING SOON"

            p_data = providers_map.get(movie.id, [])
            flat_providers = [p["provider_name"] for p in p_data]

            item = await self.create_feed_item(
                movie, None, country, tmdb,
                contributors=[{
                    "type": "upcoming",
                    "label": "Coming soon",
                    "release_badge": release_badge,
                    "release_note": release_note,
                }],
                streaming_providers=flat_providers,
            )
            seen_ids.add(movie.tmdb_id)
            items.append(item)

        if not items:
            return FeedSection(id="upcoming", title="Coming Soon", items=[])

        return FeedSection(id="upcoming", title="On Your Radar", items=items)
