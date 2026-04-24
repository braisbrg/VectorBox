import logging
import random
import asyncio
import math
import functools
from datetime import datetime
from typing import List, Dict, Set, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, or_
import numpy as np

from utils.scoring import normalize_similarity_score

from models.database import UserRating, Movie, UserCluster
from models.schemas import FeedSection, FeedItem
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.clustering_service import ClusteringService
from services.provider_service import ProviderService
from services.recommendation_service import RecommendationService
from services.embedding_service import EmbeddingService
from services.movie_service import MovieService
from services.trending_service import TrendingService
from services.profile_cache import (
    get_profile_summary_status, 
    get_cached_profile_summary, 
    set_cached_profile_summary
)
from services.cinematic_enricher import generate_profile_summary
from config import REDIS_URL
import time

from opentelemetry import trace
from telemetry import get_tracer

logger = logging.getLogger(__name__)
_tracer = get_tracer("recommendation_engine")


async def _ingest_movie_background(tmdb_id: int) -> None:
    """Background-safe movie ingestion: owns its own session, never re-raises."""
    from config import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        try:
            movie_service = MovieService(session)
            await movie_service.get_or_create_movie(tmdb_id)
            await session.commit()
        except Exception as e:
            logger.error(f"Background auto-ingest failed for tmdb_id={tmdb_id}: {e}")

# Minimum quality requirements for any movie to appear in recommendations
MOVIE_QUALITY_GATE = [
    Movie.vote_count >= 10,
    Movie.year.isnot(None),
    Movie.vectorbox_score.isnot(None),
]

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

def _score_anchor_candidate(rating, watched_date, now, watch_count: int = 1) -> float:
    """
    Combine rating quality with recency decay and rewatch boost.
    Half-life: 730 days. No floor — decay is unbounded so recent high-rated films
    correctly beat old liked-only films even when all history is 2-5 years old.
    Handles rating=None (liked-only movies) by defaulting to 3.5.
    """
    effective_rating = rating if rating is not None else 3.5
    days_ago = max(0, (now - watched_date).days) if watched_date else 730
    decay = 0.5 ** (days_ago / 730)
    rewatch_boost = min(1.0 + (watch_count - 1) * 0.15, 1.4)
    return (effective_rating / 5.0) * decay * rewatch_boost


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
        """
        Compute the average embedding vector of films the user rated <= 2 stars
        OR explicitly rejected ("Not Interested").
        Returns None if fewer than 3 such films exist (not enough signal).
        """
        # Step 1: Get internal movie_ids from UserRating (low-rated OR rejected)
        rating_result = await db.execute(
            select(UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                or_(
                    UserRating.rating <= 2.0,
                    UserRating.is_rejected.is_(True)
                )
            )
            .limit(50)
        )
        internal_ids = rating_result.scalars().all()

        if len(internal_ids) < 3:
            return None

        # Step 2: Map internal_ids → tmdb_ids via Movie table
        tmdb_result = await db.execute(
            select(Movie.tmdb_id).where(Movie.id.in_(internal_ids))
        )
        tmdb_ids = tmdb_result.scalars().all()

        if len(tmdb_ids) < 3:
            return None

        # Step 3: Fetch vectors from Qdrant in batch
        vectors_map = await qdrant.get_vectors_batch(tmdb_ids)

        if len(vectors_map) < 3:
            return None

        # Step 4: Compute element-wise mean (CPU-bound → executor)
        loop = asyncio.get_running_loop()
        vectors_list = list(vectors_map.values())

        def _compute_mean():
            arr = np.array(vectors_list)
            return np.mean(arr, axis=0).tolist()

        return await loop.run_in_executor(None, _compute_mean)

    async def _get_genre_fallback_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None
    ) -> FeedSection:
        """
        Cold start fallback: recommend high-scoring films from user's dominant genres.
        Used when Signal A or B produce empty results.
        """
        # Get user's top 3 dominant genres from their clusters
        clusters_result = await db.execute(
            select(UserCluster)
            .where(UserCluster.user_id == user_id)
            .order_by(desc(UserCluster.movie_count))
        )
        clusters = clusters_result.scalars().all()

        fallback_genres = ["Drama", "Thriller"]  # defaults
        for c in clusters:
            if c.dominant_genres:
                fallback_genres = c.dominant_genres[:3]
                break

        # Get watched movie IDs to exclude
        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
        )
        watched_ids = set(watched_result.scalars().all())

        # Query DB for high-score unwatched films matching genres (array overlap)
        candidates_result = await db.execute(
            select(Movie)
            .where(*MOVIE_QUALITY_GATE)
            .where(
                Movie.vectorbox_score > 70,
                Movie.genres.overlap(fallback_genres)
            )
            .order_by(desc(Movie.vectorbox_score))
            .limit(200)
        )
        candidates = candidates_result.scalars().all()

        # Filter watched and seen
        filtered = [m for m in candidates if m.tmdb_id not in seen_ids and m.id not in watched_ids][:10]

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
                movie, 0.85, country, tmdb,
                include_rating=True, provider_service=provider_service,
                streaming_providers=s_providers
            )
            items.append(item)
            seen_ids.add(movie.tmdb_id)

        return FeedSection(id="genre_fallback", title="Recommended for You", items=items)

    async def create_feed_item(self, movie: Movie, score: float, country: str, tmdb: TMDBClient, include_rating: bool = False, contributors: List[dict] = None, provider_service: ProviderService = None, streaming_providers: List[str] = None) -> FeedItem:
        """Helper to create a FeedItem from a Movie (DB Object)"""
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
        
        final_score = normalize_similarity_score(score)

        return FeedItem(
            id=movie.tmdb_id,
            title=movie.title,
            poster_url=movie.poster_path,
            match_score=round(final_score, 0),
            streaming_providers=list(set(streaming_providers)),
            year=movie.year,
            runtime=movie.runtime,
            letterboxd_uri=movie.letterboxd_uri,
            rating=movie.vote_average,
            overview=movie.overview,
            contributors=contributors or [],
            vectorbox_score=movie.vectorbox_score,
            imdb_rating=movie.imdb_rating,
            metacritic_rating=movie.metacritic_rating,
            rotten_tomatoes_rating=movie.rotten_tomatoes_rating,
            title_es=movie.title_es,
            overview_es=movie.overview_es,
            release_dates=movie.release_dates
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
        precomputed_anti_vector = None
    ) -> FeedSection:
        """Signal A: Because you watched [Movie X] — Item-Item Collaborative Filtering"""
        with _tracer.start_as_current_span("trident.signal_a.because_you_watched") as span:
            span.set_attribute("user_id", user_id)
            span.set_attribute("country", country)

            # FIX 6: Fetch 100 candidates without ORDER BY — Python sorts by _score_anchor_candidate
            # so NULL dates and liked-only movies don't beat a high-rated recent watch.
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
                .limit(100)
            )

            candidates = result.all()
            if not candidates:
                span.set_attribute("result_count", 0)
                # Imp 9: Cold start fallback
                return await self._get_genre_fallback_section(user_id, db, tmdb, seen_ids, country, provider_service)

            # Sort entirely in Python — _score_anchor_candidate handles rating=None and NULL dates
            now = datetime.utcnow()
            scored_candidates = []
            for row in candidates:
                user_rating, movie = row
                anchor_score = _score_anchor_candidate(
                    rating=user_rating.rating,
                    watched_date=user_rating.watched_date or user_rating.created_at,
                    now=now,
                    watch_count=getattr(user_rating, 'watch_count', 1) or 1
                )
                scored_candidates.append((anchor_score, user_rating, movie))

            scored_candidates.sort(key=lambda x: x[0], reverse=True)

            # FIX 4: Reuse precomputed anti-vector if available (avoids second Qdrant/DB round-trip)
            anti_vector = precomputed_anti_vector if precomputed_anti_vector is not None else await self._get_anti_vector(user_id, db, qdrant)
            anti_vector_np = np.array(anti_vector) if anti_vector else None

            for _, user_rating, anchor_movie in scored_candidates:
                logger.info(f"[Because you watched] Anchor: {anchor_movie.title} ({anchor_movie.year}) rating={user_rating.rating} watch_count={getattr(user_rating, 'watch_count', 1)}")

                if not self.embedding_service:
                    # No embedding service — fall back to stored vector
                    anchor_vector = await qdrant.get_vector(anchor_movie.tmdb_id)
                else:
                    if anchor_movie.keywords is None:
                        keywords = await tmdb.get_movie_keywords(anchor_movie.tmdb_id) or []
                    else:
                        keywords = anchor_movie.keywords
                    loop = asyncio.get_event_loop()
                    anchor_vector = await loop.run_in_executor(
                        None,
                        lambda: self.embedding_service.generate_embedding({
                            "title": anchor_movie.title,
                            "overview": anchor_movie.overview or "",
                            "genres": anchor_movie.genres or [],
                            "keywords": keywords
                        }, include_title=False).tolist()
                    )
                
                if not anchor_vector:
                     anchor_vector = await qdrant.get_vector(anchor_movie.tmdb_id)
                
                if not anchor_vector:
                     continue

                similar_results = await qdrant.search_similar(
                    query_vector=anchor_vector,
                    limit=500,
                    score_threshold=0.25
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
                        .where(*MOVIE_QUALITY_GATE)
                        .where(Movie.vectorbox_score >= 55)
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
                        for tid, vec in candidate_vectors.items():
                            vec_np = np.array(vec)
                            norm_product = np.linalg.norm(vec_np) * np.linalg.norm(anti_vector_np)
                            if norm_product > 0:
                                cos_sim = np.dot(vec_np, anti_vector_np) / norm_product
                                if cos_sim > 0.80:
                                    scores_map[tid] = scores_map.get(tid, 0) * 0.3
                                elif cos_sim > 0.65:
                                    scores_map[tid] = scores_map.get(tid, 0) * 0.6

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
                        if len(mmr_candidates) >= 50:
                            break

                if not mmr_candidates:
                    continue

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
                    mmr_func = functools.partial(
                        self.clustering.mmr_rerank,
                        mmr_candidates, vectors_map_mmr, 15, lambda_param=0.5
                    )
                    mmr_results = await loop.run_in_executor(None, mmr_func)
                except Exception as e:
                    logger.error(f"MMR failed in Signal A, falling back to top-15: {e}")
                    mmr_results = mmr_candidates[:15]

                items = []
                for cand in mmr_results:
                    movie = cand["movie"]
                    p_data = providers_map.get(movie.id, [])
                    s_providers = [p["provider_name"] for p in p_data]

                    item = await self.create_feed_item(
                        movie, cand["score"], country, tmdb,
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
        background_tasks=None
    ) -> FeedSection:
        """
        Global evocative theme recommendations. Rotates through GLOBAL_THEMES.
        DB-first, genre-strict, quality-gated, with score randomization for variety.
        """
        import redis.asyncio as aioredis
        from config import REDIS_URL, FEED_CACHE_VERSION
        import random

        r = None
        theme_index = 0
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

        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.is_watched.is_(True))
        )
        watched_ids = set(watched_result.scalars().all())

        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy import cast, String

        include_array = cast(theme["include_genres"], ARRAY(String))

        query = (
            select(Movie)
            .where(Movie.genres.overlap(include_array))
            .where(Movie.vectorbox_score >= theme["min_score"])
            .where(Movie.vote_count >= theme["min_votes"])
            .where(Movie.vote_average >= 5.0)
            .where(Movie.year.isnot(None))
            .where(Movie.vectorbox_score.isnot(None))
        )
        if watched_ids:
            query = query.where(Movie.id.notin_(watched_ids))

        if "max_year" in theme:
            query = query.where(Movie.year <= theme["max_year"])
        if "min_runtime" in theme:
            query = query.where(Movie.runtime >= theme["min_runtime"])
        if "original_language_not" in theme:
            query = query.where(Movie.original_language != theme["original_language_not"])
        if "max_popularity" in theme:
            query = query.where(Movie.popularity <= theme["max_popularity"])

        candidates_result = await db.execute(query.limit(200))
        candidates = candidates_result.scalars().all()

        exclude_genres = set(theme.get("exclude_genres", []))
        require_any = set(theme.get("require_any", []))
        filtered = []
        for movie in candidates:
            if movie.tmdb_id in seen_ids:
                continue
            if exclude_genres and set(movie.genres or []) & exclude_genres:
                continue
            if require_any and not (set(movie.genres or []) & require_any):
                continue
            filtered.append(movie)

        if not filtered:
            return FeedSection(id="niche_picks", title=theme["title"], items=[])

        scored = [
            (movie, (movie.vectorbox_score or 0) * random.uniform(0.7, 1.3))
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
                movie, 1.0, country, tmdb,
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
        background_tasks = None
    ) -> FeedSection:
        """Signal C: Hidden Gems — Score-to-Hype Filtering"""
        with _tracer.start_as_current_span("trident.signal_c.hidden_gems") as span:
            span.set_attribute("user_id", user_id)
            span.set_attribute            # Step 1: Get dynamic thresholds based on user's movie count
            user_count_result = await db.execute(
                select(func.count(UserRating.id))
                .where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
            )
            user_movie_count = user_count_result.scalar() or 0
            thresholds = _get_signal_c_thresholds(user_movie_count)
            logger.info(f"[Signal C] User {user_id} has {user_movie_count} movies, using thresholds: {thresholds}")
            
            # Step 2: DB Query — Fetch high-quality unwatched candidates
            # We exclude watched_tmdb_ids (converted to internal IDs)
            watched_result = await db.execute(
                select(UserRating.movie_id)
                .where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
            )
            watched_internal_ids = set(watched_result.scalars().all())
            
            result = await db.execute(
                select(Movie)
                .where(*MOVIE_QUALITY_GATE)
                .where(Movie.vectorbox_score >= thresholds["min_score"])
                .where(Movie.popularity <= thresholds["max_popularity"])
                .where(Movie.vote_count >= thresholds["min_votes"])
                .where(Movie.id.notin_(watched_internal_ids) if watched_internal_ids else True)
                .order_by(desc(Movie.vectorbox_score))
                .limit(200)
            )
            candidates = result.scalars().all()
            
            if not candidates:
                span.set_attribute("result_count", 0)
                return FeedSection(id="hidden_gems", title="Hidden Gems", items=[])

            # Step 3: Get user's global center vector (from cluster medoids)
            clusters_result = await db.execute(
                select(UserCluster).where(UserCluster.user_id == user_id)
            )
            clusters = clusters_result.scalars().all()
            
            global_center = None
            if clusters:
                # Use mean of medoid vectors
                medoid_ids = [c.medoid_movie_id for c in clusters if c.medoid_movie_id]
                if medoid_ids:
                    medoid_movies = await db.execute(select(Movie.tmdb_id).where(Movie.id.in_(medoid_ids)))
                    medoid_tmdb_ids = medoid_movies.scalars().all()
                    vectors_map = await self.qdrant.get_vectors_batch(medoid_tmdb_ids)
                    if vectors_map:
                        global_center = np.mean(list(vectors_map.values()), axis=0)
            
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
                    movie, cand["score"], country, tmdb,
                    include_rating=True, provider_service=provider_service,
                    streaming_providers=s_providers
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
                    match_score = 95
                    if movie.vote_average:
                        match_score = 90 + min(9, max(0, (movie.vote_average - 7.0) * 4.5))
                    
                    items.append(FeedItem(
                        id=movie.tmdb_id,
                        title=movie.title,
                        poster_url=movie.poster_path,
                        match_score=round(match_score, 0),
                        streaming_providers=list(set(available_providers)),
                        year=movie.year,
                        runtime=movie.runtime,
                        letterboxd_uri=movie.letterboxd_uri,
                        rating=movie.vote_average,
                        vectorbox_score=movie.vectorbox_score,
                        imdb_rating=movie.imdb_rating,
                        metacritic_rating=movie.metacritic_rating,
                        rotten_tomatoes_rating=movie.rotten_tomatoes_rating,
                        title_es=movie.title_es,
                        overview_es=movie.overview_es,
                        overview=movie.overview,
                        contributors=[{
                            "type": "watchlist",
                            "label": "In your watchlist",
                        }],
                    ))
                    seen_ids.add(movie.tmdb_id)
                    if len(items) >= 20:
                        break
        
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
        provider_service: ProviderService = None
    ) -> Optional[FeedSection]:
        """Wildcard Section: Outside Your Comfort Zone"""
        clusters_result = await db.execute(
            select(UserCluster)
            .where(UserCluster.user_id == user_id)
            .order_by(desc(UserCluster.movie_count))
            .limit(3)
        )
        clusters = clusters_result.scalars().all()
        
        excluded_genres = set()
        for c in clusters:
            if c.dominant_genres:
                excluded_genres.update(c.dominant_genres)
        
        if not excluded_genres:
            return None

        # FIX 5: Push genre exclusion and watched filter to DB; use func.random() to avoid 1000-row scan
        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
        )
        watched_internal_ids = set(watched_result.scalars().all())

        excluded_array = list(excluded_genres)
        q = (
            select(Movie)
            .where(*MOVIE_QUALITY_GATE)
            .where(Movie.vectorbox_score >= 45)
            .where(Movie.vote_average > 7.0)
            .where(Movie.vote_count > 100)
            .where(~Movie.genres.overlap(excluded_array))
        )
        if watched_internal_ids:
            q = q.where(Movie.id.notin_(watched_internal_ids))
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
            item = await self.create_feed_item(m, 0.85, country, tmdb, include_rating=True, streaming_providers=flat_providers)
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
        provider_service: ProviderService = None
    ) -> Optional[FeedSection]:
        """Random Picks"""
        # FIX 5: Push watched filter to DB and use func.random() — avoids 500-row scan
        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
        )
        watched_internal_ids = set(watched_result.scalars().all())

        q = (
            select(Movie)
            .where(*MOVIE_QUALITY_GATE)
            .where(Movie.vectorbox_score.between(1, 98))
        )
        if watched_internal_ids:
            q = q.where(Movie.id.notin_(watched_internal_ids))
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
            item = await self.create_feed_item(m, 0.9, country, tmdb, include_rating=True, streaming_providers=flat_providers)
            items.append(item)
            seen_ids.add(m.tmdb_id)
            
        return FeedSection(
            id="random_picks", 
            title="Random Top Picks", 
            items=items
        )

    async def get_popular_on_letterboxd_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        country: str,
        provider_service: ProviderService = None
    ) -> Optional[FeedSection]:
        """Popular on Letterboxd"""
        trending_service = TrendingService(db)
        try:
            popular_ids = await trending_service.get_popular_movie_ids()
        finally:
            await trending_service.close()

        if not popular_ids:
            return None
            
        result = await db.execute(
            select(Movie)
            .where(Movie.tmdb_id.in_(popular_ids))
            .where(*MOVIE_QUALITY_GATE)
        )
        fetched_movies = result.scalars().all()
        movies_map = {m.tmdb_id: m for m in fetched_movies}

        # FIX 8: Filter out movies the user has already watched
        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
        )
        watched_internal_ids = set(watched_result.scalars().all())
        # Map internal IDs → tmdb_ids for comparison
        if watched_internal_ids:
            watched_tmdb_result = await db.execute(
                select(Movie.tmdb_id).where(Movie.id.in_(watched_internal_ids))
            )
            watched_tmdb_ids = set(watched_tmdb_result.scalars().all())
        else:
            watched_tmdb_ids = set()

        # Batch-fetch providers (no N+1)
        if provider_service and fetched_movies:
            internal_ids = [m.id for m in fetched_movies]
            providers_map = await provider_service.get_providers_batch(internal_ids, country)
        else:
            providers_map = {}
        
        items = []
        for tmdb_id in popular_ids:
            if tmdb_id in watched_tmdb_ids:
                continue
            movie = movies_map.get(tmdb_id)
            if movie:
                p_data = providers_map.get(movie.id, [])
                flat_providers = [p["provider_name"] for p in p_data]
                item = await self.create_feed_item(
                    movie, 0.95, country, tmdb, include_rating=True,
                    streaming_providers=flat_providers
                )
                if movie.letterboxd_rating:
                    item.letterboxd_rating = movie.letterboxd_rating
                items.append(item)
                
        if not items:
            return None
            
        return FeedSection(
            id="popular_letterboxd",
            title="Popular on Letterboxd This Week",
            items=items
        )

    async def get_random_watchlist_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        country: str,
        provider_service: ProviderService = None
    ) -> Optional[FeedSection]:
        """Get random movies from the user's watchlist."""
        result = await db.execute(
            select(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                UserRating.is_watchlist.is_(True),
                UserRating.is_watched.is_(False)
            )
            .order_by(func.random())
            .limit(20)
        )
        selected_movies = result.scalars().all()

        if not selected_movies:
            return None

        # Batch-fetch providers (no N+1 / no per-movie TMDB calls)
        if provider_service and selected_movies:
            sample_ids = [m.id for m in selected_movies]
            providers_map = await provider_service.get_providers_batch(sample_ids, country)
        else:
            providers_map = {}
        
        items = []
        for movie in selected_movies:
            p_data = providers_map.get(movie.id, [])
            flat_providers = [p["provider_name"] for p in p_data]
            item = await self.create_feed_item(
                movie, 1.0, country, tmdb, include_rating=True,
                streaming_providers=flat_providers
            )
            items.append(item)
            
        return FeedSection(
            id="random_watchlist",
            title="Shuffle: From Your Watchlist",
            items=items
        )
