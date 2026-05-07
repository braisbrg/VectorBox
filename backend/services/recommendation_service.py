import logging
import asyncio
from typing import List, Dict, Set, Optional, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, or_, func, text, cast, String
from collections import Counter
import random
import math
import json
import hashlib
import numpy as np
import redis.asyncio as redis

from models.database import UserRating, Movie, UserCluster, User
from models.schemas import FeedSection, FeedItem
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.clustering_service import ClusteringService
from services.movie_service import MovieService
from services.provider_service import ProviderService

from utils.decorators import safe_execution

logger = logging.getLogger(__name__)

MIN_QUALITY_SCORE = 55  # floor for Picked For You; pre-filtered into Signal A and re-checked in hybrid_reranking
MIN_SIGNAL_C_SCORE = 62  # sweet spot between 55 (too permissive) and 68 (too strict)
MIN_EMBED_QUALITY_SCORE = 0.35  # below this is MiniLM-only noise; produces false centroid matches

# Generic genres co-occur across most films and don't tell us anything about user taste.
# Removed before computing the user's "distinctive" genre set for Signal A coherence.
GENERIC_GENRES = {"Action", "Drama", "Comedy", "Adventure", "Thriller"}

# Anti-vector penalty thresholds — same as Because You Watched (recommendation_engine.py:544-557).
ANTI_VECTOR_DROP_THRESHOLD = 0.80
ANTI_VECTOR_DEMOTE_THRESHOLD = 0.65
ANTI_VECTOR_DEMOTE_FACTOR = 0.5
ANTI_VECTOR_BATCH_LIMIT = 30  # bound batch fetch cost; tail of raw_recs left untouched


async def _ingest_movie_rs_background(tmdb_id: int) -> None:
    """Background task: ingest a missing movie using its own DB session."""
    from config import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        try:
            movie_service = MovieService(session)
            await movie_service.get_or_create_movie(tmdb_id)
            await session.commit()
        except Exception as e:
            logger.error(f"Background ingest failed for tmdb_id={tmdb_id}: {e}")

class RecommendationService:
    """
    The "Trident" Hybrid Recommender System.
    Merges 3 distinct signals:
    - Signal A: Vibe (Vector Embeddings)
    - Signal Auteur: Director Analysis
    - Signal C: Crowd (TMDB Collaborative Filtering)
    """

    def __init__(self, db: AsyncSession, tmdb: TMDBClient = None, qdrant: QdrantService = None, redis_client: redis.Redis = None):
        self.db = db
        self.tmdb = tmdb
        self.qdrant = qdrant
        self.redis = redis_client
        self.clustering = ClusteringService(qdrant=qdrant)
        self.movie_service = MovieService(db, tmdb=tmdb)

    @safe_execution(fallback_return=FeedSection(id="picked_for_you", title="Picked For You (Signal Lost)", items=[]))
    async def get_hybrid_picks_section(
        self, 
        user_id: int, 
        country: str,
        seen_ids: Set[int],
        provider_service: ProviderService = None,
        background_tasks = None
    ) -> FeedSection:
        """
        Main entry point for "The Trident" row.
        """
        logger.info(f"Generating Trident Hybrid Picks for User {user_id}")
        
        # 1. Gather Signals in Parallel
        import time
        start_time = time.time()
        
        # Define wrappers to measure individual signal time and provide an isolated DB session
        async def measure_signal(name, method_name, *args, **kwargs):
            t0 = time.time()
            from config import AsyncSessionLocal
            async with AsyncSessionLocal() as session:
                # Create an isolated service instance for this task to avoid concurrent session errors
                isolated_service = RecommendationService(db=session, tmdb=self.tmdb, qdrant=self.qdrant, redis_client=self.redis)
                method = getattr(isolated_service, method_name)
                res = await method(*args, **kwargs)
            duration = (time.time() - t0) * 1000
            logger.info(f"[TRIDENT] Signal {name} took {duration:.0f}ms")
            return res

        signal_a_task = measure_signal("A (Vibe)", "get_signal_a_vibe", user_id, exclude_ids=seen_ids, background_tasks=background_tasks)
        signal_b_task = measure_signal("Auteur", "get_signal_b_auteur", user_id, exclude_ids=seen_ids)
        signal_c_task = measure_signal("C (Crowd)", "get_signal_c_crowd", user_id, exclude_ids=seen_ids, background_tasks=background_tasks)
        
        results = await asyncio.gather(signal_a_task, signal_b_task, signal_c_task, return_exceptions=True)
        
        total_time = (time.time() - start_time) * 1000
        logger.info(f"[TRIDENT] Full Trident gathering took {total_time:.2f}ms")
        
        # Handle exceptions gracefully
        candidates_lists = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"Signal {['A', 'Auteur', 'C'][i]} failed: {res}")
                candidates_lists.append([])
            else:
                candidates_lists.append(res)
                
        signal_a, signal_b, signal_c = candidates_lists

        logger.info(f"[TRIDENT] Signal Counts -> A: {len(signal_a)}, Auteur: {len(signal_b)}, C: {len(signal_c)}")

        # Build per-signal score maps for contributor provenance (A3)
        signal_a_ids = {m.id: 1 / (60 + i) for i, m in enumerate(signal_a)}
        signal_b_ids = {m.id: 1 / (60 + i) for i, m in enumerate(signal_b)}
        signal_c_ids = {m.id: 1 / (60 + i) for i, m in enumerate(signal_c)}

        # 2. Fusion (RRF)
        # We assume candidates are Movie objects (or dicts representing them)
        # We need uniform ID access. Let's make sure signals return Movie objects.

        rrf_scores = self.reciprocal_rank_fusion([signal_a, signal_b, signal_c])

        # 3. Post-Processing (Quality & Diversity)
        final_items = await self.hybrid_reranking(
            rrf_scores, user_id, country, provider_service,
            signal_a_ids=signal_a_ids,
            signal_b_ids=signal_b_ids,
            signal_c_ids=signal_c_ids
        )
        
        # Update seen_ids
        for item in final_items:
            seen_ids.add(item.id)
            
        return FeedSection(
            id="picked_for_you",
            title="Picked For You",
            items=final_items
        )

    async def _get_signal_with_cache_and_lock(self, user: User, signal_type: str, params: Dict[str, Any], compute_method) -> List[Movie]:
        """
        Fetches a signal, utilizing Redis cache with a SETNX-based locking mechanism
        to prevent cache stampedes.
        """
        if not self.redis:
            logger.warning(f"Redis client not available for {signal_type} signal. Computing without cache/lock.")
            return await compute_method(user.id, **params)

        # Strip non-serializable params (e.g. BackgroundTasks) from the cache key
        serializable_params = {k: v for k, v in params.items() if k != "background_tasks"}
        # FIX 4: Hash params to avoid multi-KB Redis keys from large exclude_ids lists
        params_hash = hashlib.md5(json.dumps(serializable_params, sort_keys=True).encode()).hexdigest()[:12]
        cache_key = f"signal_cache:{user.id}:{signal_type}:{params_hash}"
        
        # Try to get from cache first
        cached = await self.redis.get(cache_key)
        if cached:
            logger.info(f"Cache hit for {signal_type} signal for user {user.id}")
            data = json.loads(cached)
            # Assuming data is a list of movie IDs, fetch Movie objects
            movie_ids = [item["movie_id"] for item in data]
            if not movie_ids:
                return []
            stmt = select(Movie).where(Movie.id.in_(movie_ids))
            result = await self.db.execute(stmt)
            movies = result.scalars().all()
            # Re-sort based on cached order
            movies_map = {m.id: m for m in movies}
            ordered_movies = [movies_map[mid] for mid in movie_ids if mid in movies_map]
            return ordered_movies

        # Cache Miss - Recompute with Lock to prevent cache stampedes (Fix 2.3/4.1)
        lock_key = f"lock:{cache_key}"
        lock_acquired = await self.redis.setnx(lock_key, "locked")
        
        if lock_acquired:
            # We got the lock! We must compute, set the cache, and release the lock.
            try:
                # Set a short expiration on the lock itself as a safety net
                await self.redis.expire(lock_key, 30) # 30 seconds expiration
                
                # Signal Generation
                result = await compute_method(user.id, **params)

                # Cache Result
                signal_data = [
                    {"movie_id": s.id, "score": 1.0} # Simplified score for caching, actual score is in RRF
                    for s in result
                ]
                await self.redis.setex(
                    cache_key,
                    86400, # 24h TTL
                    json.dumps(signal_data)
                )
                
            finally:
                # Always release the lock
                await self.redis.delete(lock_key)
                
            return result
            
        else:
            # Did not get the lock - another worker is computing it. Wait and poll.
            logger.info(f"Cache lock held for {cache_key}. Waiting...")
            retries = 30 # Up to 3 seconds wait
            while retries > 0:
                await asyncio.sleep(0.1)
                cached = await self.redis.get(cache_key)
                if cached:
                    logger.info(f"Cache miss resolved by another worker for: {cache_key}")
                    data = json.loads(cached)
                    movie_ids = [item["movie_id"] for item in data]
                    if not movie_ids:
                        return []
                    stmt = select(Movie).where(Movie.id.in_(movie_ids))
                    result = await self.db.execute(stmt)
                    movies = result.scalars().all()
                    movies_map = {m.id: m for m in movies}
                    ordered_movies = [movies_map[mid] for mid in movie_ids if mid in movies_map]
                    return ordered_movies
                retries -= 1
            
            # If we timeout waiting, fallback to computing it anyway
            logger.warning(f"Timeout waiting for lock {lock_key}. Computing fallback.")
            return await compute_method(user.id, **params)

    async def get_signal_a_vibe(self, user_id: int, exclude_ids: Set[int], background_tasks = None) -> List[Movie]:
        """
        Signal A: The Vibe Expert (Vectors)
        Uses Qdrant via ClusteringService logic.
        """
        # Fetch user object for _get_signal_with_cache_and_lock
        user_obj = await self.db.get(User, user_id)
        if not user_obj:
            logger.warning(f"User {user_id} not found for Vibe signal.")
            return []

        return await self._get_signal_with_cache_and_lock(
            user=user_obj,
            signal_type="vibe",
            params={"exclude_ids": list(exclude_ids), "background_tasks": background_tasks},
            compute_method=self._compute_vibe_signal_raw
        )

    async def _get_anti_vector(self, user_id: int) -> Optional[List[float]]:
        """Progressive anti-vector with recency decay — see
        RecommendationEngine._get_anti_vector (recommendation_engine.py) for
        the policy. Duplicated here to avoid the engine→service import cycle.
        Returns L2-normalized weighted mean, or None when fewer than 3
        negative films have vectors.

        Each weight is multiplied by a recency decay factor with a 365-day
        half-life, so old rejections/low ratings gradually lose influence.
        """
        from datetime import datetime, timezone

        rating_result = await self.db.execute(
            select(UserRating, Movie.tmdb_id)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(
                or_(
                    UserRating.is_rejected.is_(True),
                    UserRating.rating <= 3.0,
                )
            )
            .limit(50)
        )
        rows = rating_result.all()
        if len(rows) < 3:
            return None

        tmdb_ids = [tmdb_id for _, tmdb_id in rows if tmdb_id is not None]
        if len(tmdb_ids) < 3:
            return None
        vectors_map = await self.qdrant.get_vectors_batch(tmdb_ids)
        if len(vectors_map) < 3:
            return None

        now = datetime.now(timezone.utc)
        HALF_LIFE_DAYS = 365

        weighted_vectors: list[np.ndarray] = []
        weights: list[float] = []
        for ur, tmdb_id in rows:
            vec = vectors_map.get(tmdb_id)
            if vec is None:
                continue
            if ur.is_rejected:
                w = 2.0
            elif ur.rating is None:
                continue
            elif ur.rating <= 2.0:
                w = 1.5
            elif ur.rating <= 2.5:
                w = 1.0
            elif ur.rating <= 3.0:
                w = 0.4
            else:
                continue

            # Recency decay: 365-day half-life
            ref_date = ur.watched_date or ur.created_at
            if ref_date is not None:
                if ref_date.tzinfo is None:
                    ref_date = ref_date.replace(tzinfo=timezone.utc)
                days_ago = max(0, (now - ref_date).days)
            else:
                days_ago = HALF_LIFE_DAYS  # assume 1 half-life if undated
            decay = 0.5 ** (days_ago / HALF_LIFE_DAYS)
            w *= decay

            if w < 0.05:
                continue  # negligible weight — skip

            weighted_vectors.append(np.array(vec) * w)
            weights.append(w)

        if len(weighted_vectors) < 3:
            return None

        loop = asyncio.get_running_loop()

        def _compute_mean():
            total_w = float(sum(weights))
            mean_vec = np.sum(np.stack(weighted_vectors), axis=0) / total_w
            norm = float(np.linalg.norm(mean_vec))
            if norm > 0:
                mean_vec = mean_vec / norm
            return mean_vec.tolist()

        return await loop.run_in_executor(None, _compute_mean)

    async def _filter_by_anti_vector(
        self,
        items: List[Tuple[Movie, str]],
        anti_vector: Optional[List[float]],
        drop_threshold: float = 0.75,
    ) -> Tuple[List[Tuple[Movie, str]], int]:
        """Drop (movie, label) pairs whose embedding is too close to the anti-vector.

        Returns (kept_items, dropped_count). When anti_vector is None or no item
        has a vector, returns the original list unchanged so we never block
        a section on a missing negative signal.
        """
        if not anti_vector or not items:
            return items, 0
        tmdb_ids = [m.tmdb_id for m, _ in items]
        vectors_map = await self.qdrant.get_vectors_batch(tmdb_ids)
        if not vectors_map:
            return items, 0
        anti_np = np.array(anti_vector)
        anti_norm = float(np.linalg.norm(anti_np))
        if anti_norm == 0:
            return items, 0
        kept: List[Tuple[Movie, str]] = []
        dropped = 0
        for movie, label in items:
            vec = vectors_map.get(movie.tmdb_id)
            if vec is None:
                kept.append((movie, label))
                continue
            cand = np.array(vec)
            denom = float(np.linalg.norm(cand)) * anti_norm
            if denom == 0:
                kept.append((movie, label))
                continue
            cos_sim = float(np.dot(cand, anti_np) / denom)
            if cos_sim > drop_threshold:
                dropped += 1
                continue
            kept.append((movie, label))
        return kept, dropped

    async def _get_distinctive_user_genres(self, user_id: int) -> Set[str]:
        """Top genres in user's rated history minus the generic set.

        Delegates to the shared utility in utils.genre_utils to avoid
        duplicated query logic and circular import issues.
        """
        from utils.genre_utils import get_distinctive_user_genres
        return await get_distinctive_user_genres(user_id, self.db)

    async def _compute_vibe_signal_raw(self, user_id: int, exclude_ids: Set[int], background_tasks = None) -> List[Movie]:
        """
        Raw computation for Signal A: The Vibe Expert (Vectors)
        """
        raw_recs = await self.clustering.get_user_centric_recommendations(
            user_id=user_id,
            db=self.db,
            filters={"min_vote_count": 500}, # Basic quality filter
            limit=50,
            background_tasks=background_tasks
        )

        # FIX 1: Anti-vector penalty (parity with Because You Watched at
        # recommendation_engine.py:544-557). The centroid path previously had no
        # anti-vector; mass-appeal blockbusters that sit near the generic taste mean
        # slipped in regardless of how strongly the user disliked similar films.
        # Bounded to top-30 to keep the extra Qdrant batch fetch cheap.
        anti_vector = await self._get_anti_vector(user_id)
        anti_dropped = anti_demoted = 0
        if anti_vector and raw_recs:
            anti_np = np.array(anti_vector)
            anti_norm = float(np.linalg.norm(anti_np))
            if anti_norm > 0:
                head = raw_recs[: ANTI_VECTOR_BATCH_LIMIT]
                tail = raw_recs[ANTI_VECTOR_BATCH_LIMIT :]
                head_ids = [r["movie_id"] for r in head]
                cand_vec_map = await self.qdrant.get_vectors_batch(head_ids)

                adjusted_head: List[Dict] = []
                for r in head:
                    vec = cand_vec_map.get(r["movie_id"])
                    if vec is None:
                        adjusted_head.append(r)
                        continue
                    cand_np = np.array(vec)
                    cand_norm = float(np.linalg.norm(cand_np))
                    if cand_norm == 0:
                        adjusted_head.append(r)
                        continue
                    cos_sim = float(np.dot(cand_np, anti_np) / (cand_norm * anti_norm))
                    if cos_sim > ANTI_VECTOR_DROP_THRESHOLD:
                        anti_dropped += 1
                        continue
                    if cos_sim > ANTI_VECTOR_DEMOTE_THRESHOLD:
                        adjusted_head.append({**r, "score": r.get("score", 1.0) * ANTI_VECTOR_DEMOTE_FACTOR})
                        anti_demoted += 1
                        continue
                    adjusted_head.append(r)

                adjusted_head.sort(key=lambda x: x.get("score", 0.0), reverse=True)
                raw_recs = adjusted_head + tail

        # Qdrant point IDs are tmdb_ids (movie_factory.py:137 / qdrant_service.py:78).
        # The previous Movie.id.in_(...) query compared internal PKs to tmdb_ids and
        # only ever matched on accidental numeric overlap, so 50 raw_recs collapsed
        # to ~2 DB hits. AGENTS.md:245 — "Qdrant vectors indexed by tmdb_id".
        tmdb_ids = [r["movie_id"] for r in raw_recs if r["movie_id"] not in exclude_ids]

        if not tmdb_ids:
            return []

        # Pre-filter by quality floor: keeps low-VB films out of RRF entirely so
        # they cannot dominate rankings before hybrid_reranking drops them.
        stmt = (
            select(Movie)
            .where(Movie.tmdb_id.in_(tmdb_ids))
            .where(Movie.vectorbox_score >= MIN_QUALITY_SCORE)
        )
        result = await self.db.execute(stmt)
        movies = result.scalars().all()
        db_match_count = len(movies)

        # Re-sort match per raw_recs order — keyed by tmdb_id, since that is what
        # tmdb_ids holds.
        movies_map = {m.tmdb_id: m for m in movies}
        ordered = []
        for tid in tmdb_ids:
            if tid in movies_map:
                ordered.append(movies_map[tid])

        # FIX 2: Genre coherence — drop candidates that share no distinctive genre
        # with the user's rated history. Without this, the centroid of a diverse
        # taste profile (drama + animation + family) lands on a non-distinctive
        # midpoint and lets generic action/family blockbusters surface. Films with
        # no genre metadata or whose genres are entirely within the generic set are
        # allowed through (they cannot fail the test on signal alone).
        before_genre = len(ordered)
        distinctive = await self._get_distinctive_user_genres(user_id)
        if distinctive:
            kept = []
            for m in ordered:
                m_genres = set(m.genres or [])
                if not m_genres:
                    kept.append(m)
                    continue
                if m_genres & distinctive:
                    kept.append(m)
                    continue
                if not (m_genres - GENERIC_GENRES):
                    kept.append(m)
                    continue
            ordered = kept
        after_genre_count = len(ordered)

        # T-04: Drop films with corrupt MiniLM-only embeddings — they reach the
        # centroid via accidental proximity, not real cinematic similarity.
        # NULL = unchecked (allow through), < 0.35 = noisy and worth dropping.
        ordered = [
            m for m in ordered
            if m.embedding_quality_score is None
            or m.embedding_quality_score >= MIN_EMBED_QUALITY_SCORE
        ]

        logger.info(
            f"[Signal A] user={user_id} qdrant={len(raw_recs)} "
            f"after_exclude={len(tmdb_ids)} db_matches={db_match_count} "
            f"after_quality={before_genre} after_genre={after_genre_count} "
            f"after_embed_quality={len(ordered)} "
            f"anti_dropped={anti_dropped} anti_demoted={anti_demoted} "
            f"distinctive_genres={sorted(distinctive)}"
        )
        return ordered

    async def get_signal_b_auteur(self, user_id: int, exclude_ids: Set[int]) -> List[Movie]:
        """
        Signal Auteur: The Auteur Expert (Metadata Graph)
        Finds user's top directors and recommends their high-quality unwatched movies.
        """
        user_obj = await self.db.get(User, user_id)
        if not user_obj:
            logger.warning(f"User {user_id} not found for Auteur signal.")
            return []

        return await self._get_signal_with_cache_and_lock(
            user=user_obj,
            signal_type="auteur",
            params={"exclude_ids": list(exclude_ids)},
            compute_method=self._compute_auteur_signal_raw
        )

    async def _compute_director_scores(self, user_id: int) -> Dict[str, float]:
        """
        Weighted director scoring used by the Auteur signal and rotation logic.
        Applies recency decay (730-day half-life) and saga penalty.
        """
        from services.recommendation_engine import _director_weight
        from datetime import datetime, timezone

        stmt = select(UserRating, Movie).join(Movie, UserRating.movie_id == Movie.id)\
        .where(
            UserRating.user_id == user_id,
            or_(
                UserRating.rating >= 3.0,
                UserRating.is_liked.is_(True)
            )
        )
        result = await self.db.execute(stmt)
        rated_movies = result.all()
        if not rated_movies:
            return {}

        now = datetime.now(timezone.utc)
        director_appearances: Dict[str, int] = {}
        for _, movie in rated_movies:
            if not movie.directors:
                continue
            for director in movie.directors:
                director_appearances[director] = director_appearances.get(director, 0) + 1

        director_scores: Dict[str, float] = {}
        for rating_obj, movie in rated_movies:
            if not movie.directors:
                continue
            effective_rating = rating_obj.rating or 4.5
            base_weight = _director_weight(effective_rating)
            if base_weight == 0:
                continue

            wd = rating_obj.watched_date
            if wd is not None:
                if wd.tzinfo is None:
                    wd = wd.replace(tzinfo=timezone.utc)
                days_ago = max(0, (now - wd).days)
            else:
                days_ago = 730
            decay = 0.5 ** (days_ago / 730)

            for director in movie.directors:
                appearances = director_appearances.get(director, 1)
                saga_penalty = 1.0 / (1.0 + max(0, appearances - 3) * 0.3)
                final_weight = base_weight * decay * saga_penalty
                director_scores[director] = director_scores.get(director, 0) + final_weight

        return director_scores

    async def _compute_auteur_signal_raw(self, user_id: int, exclude_ids: Set[int]) -> List[Movie]:
        """
        Raw computation for Signal Auteur: The Auteur Expert (Metadata Graph)
        Imp 8: Uses weighted point system instead of hard count threshold.
        """
        director_scores = await self._compute_director_scores(user_id)
        if not director_scores:
            return []

        # Imp 8: Director activates at >= 3.0 points
        top_directors = [name for name, score in sorted(director_scores.items(), key=lambda x: x[1], reverse=True) if score >= 3.0][:5]

        logger.info(
            f"[Signal Auteur] user={user_id} "
            f"directors_scored={len(director_scores)} top_directors={top_directors}"
        )
        if not top_directors:
            return []
            
        # 2. Query DB for matches
        from services.recommendation_engine import MOVIE_QUALITY_GATE
        stmt = select(Movie).where(
            *MOVIE_QUALITY_GATE,
            Movie.vectorbox_score > 70,
            Movie.directors.overlap(top_directors)
        ).limit(100)
        
        candidates = (await self.db.execute(stmt)).scalars().all()
        
        # Filter watched/excluded
        watched_stmt = select(UserRating.movie_id).where(
            UserRating.user_id == user_id, UserRating.is_watched.is_(True)
        )
        watched_ids = set((await self.db.execute(watched_stmt)).scalars().all())
        
        final_list = []
        dropped = 0
        for m in candidates:
            if m.tmdb_id in exclude_ids or m.id in watched_ids:
                dropped += 1
                continue
            final_list.append(m)

        logger.info(
            f"[Signal Auteur] user={user_id} db_candidates={len(candidates)} "
            f"dropped_watched_or_excluded={dropped} kept={len(final_list[:50])}"
        )
        return final_list[:50]

    async def get_signal_c_crowd(self, user_id: int, exclude_ids: Set[int], background_tasks = None) -> List[Movie]:
        """
        Signal C: The Crowd Expert (Collaborative Filtering via TMDB)
        "People who liked X also liked Y"
        """
        user_obj = await self.db.get(User, user_id)
        if not user_obj:
            logger.warning(f"User {user_id} not found for Crowd signal.")
            return []

        # For crowd signal, we might need a cluster_id if it's part of the logic
        # For now, we'll pass a dummy or derive it if needed.
        # Assuming no cluster_id is needed for this specific crowd signal implementation.
        return await self._get_signal_with_cache_and_lock(
            user=user_obj,
            signal_type="crowd",
            params={"exclude_ids": list(exclude_ids), "background_tasks": background_tasks},
            compute_method=self._compute_crowd_signal_raw
        )

    async def _compute_crowd_signal_raw(self, user_id: int, exclude_ids: Set[int], background_tasks = None) -> List[Movie]:
        """
        Raw computation for Signal C: The Crowd Expert (Collaborative Filtering via TMDB)
        """
        if not self.tmdb:
            logger.warning("[Signal C] No TMDBClient injected, skipping crowd signal.")
            return []
        
        # 1. Get up to 5 high-quality seed movies: 4.5★+, liked, or rewatched
        stmt = (
            select(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(
                or_(
                    UserRating.rating >= 4.5,
                    UserRating.is_liked.is_(True),
                    UserRating.watch_count > 1,
                )
            )
            .order_by(desc(UserRating.rating), desc(UserRating.watched_date))
            .limit(5)
        )

        seeds = (await self.db.execute(stmt)).scalars().all()

        logger.info(f"[Signal C] user={user_id} seeds_quality={len(seeds)}")
        if not seeds:
            return []
            
        # 2. Collect all TMDB IDs from TMDB recommendations (parallel, no sequential N+1)
        all_tmdb_ids: List[int] = []
        tasks = [self.tmdb.get_movie_recommendations(seed.tmdb_id) for seed in seeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, recs in enumerate(results):
            if isinstance(recs, Exception):
                logger.warning(f"[Signal C] TMDB recs failed for seed {seeds[i].tmdb_id}: {recs}")
                continue
            for r in recs[:5]:
                tid = r['id']
                if tid not in exclude_ids:
                    all_tmdb_ids.append(tid)

        
        if not all_tmdb_ids:
            return []

        # 3. BATCH CHECK: which IDs already exist in DB? (single query, no N+1)
        existing_result = await self.db.execute(
            select(Movie).where(Movie.tmdb_id.in_(all_tmdb_ids))
        )
        existing_movies = existing_result.scalars().all()
        existing_tmdb_ids = {m.tmdb_id for m in existing_movies}

        # 4. Ingest only the missing ones (max 5 to avoid long waits)
        missing_ids = [tid for tid in all_tmdb_ids if tid not in existing_tmdb_ids][:5]
        for tid in missing_ids:
            try:
                if background_tasks:
                    background_tasks.add_task(_ingest_movie_rs_background, tid)
                    logger.debug(f"[Signal C] Queued background ingest for TMDB {tid}")
                else:
                    logger.debug(f"[Signal C] Skipping auto-ingest for {tid} — no background_tasks")
            except Exception as e:
                logger.warning(f"[Signal C] Could not queue ingest TMDB {tid}: {e}")

        # 5. Quality threshold — sweet spot between 55 (too permissive, lets in The
        # Shack/Restless/The Number 23) and 68 (too strict, blocks artsy profiles
        # entirely). 62 keeps Synecdoche/Quiz Show/Fat City/Intolerance/Silence of
        # the Lambs while rejecting the obvious low-quality TMDB suggestions.
        signal_c_min_score = MIN_SIGNAL_C_SCORE

        # 6. Filter and deduplicate
        seen_local: Set[int] = set()
        unique: List[Movie] = []
        dropped_excluded = dropped_quality = 0
        for m in existing_movies:
            if m.id in seen_local or m.tmdb_id in exclude_ids:
                dropped_excluded += 1
                continue
            if (m.vectorbox_score or 0) < signal_c_min_score:
                dropped_quality += 1
                continue
            unique.append(m)
            seen_local.add(m.id)

        logger.info(
            f"[Signal C] user={user_id} tmdb_recs={len(all_tmdb_ids)} "
            f"in_db={len(existing_movies)} queued_ingest={len(missing_ids)} "
            f"min_score={signal_c_min_score} dropped_excluded={dropped_excluded} "
            f"dropped_quality={dropped_quality} kept={len(unique)}"
        )
        return unique

    def reciprocal_rank_fusion(self, candidate_lists: List[List[Movie]], k=60) -> Dict[int, float]:
        """
        RRF Algorithm: Merges multiple ranked lists.
        Score = sum(1 / (k + rank))
        """
        scores = {}
        movies_map = {} # To keep track of objects
        
        for lst in candidate_lists:
            for rank, movie in enumerate(lst):
                if movie.id not in scores:
                    scores[movie.id] = 0.0
                    movies_map[movie.id] = movie
                
                scores[movie.id] += 1 / (k + rank)
                
        return scores

    async def hybrid_reranking(
        self,
        rrf_scores: Dict[int, float],
        user_id: int,
        country: str,
        provider_service: ProviderService,
        signal_a_ids: Dict[int, float] = None,
        signal_b_ids: Dict[int, float] = None,
        signal_c_ids: Dict[int, float] = None,
    ) -> List[FeedItem]:
        def build_contributors(movie_id, sa, sb, sc):
            sa, sb, sc = sa or {}, sb or {}, sc or {}
            raw = []
            if movie_id in sa:
                raw.append(("vibe", "Semantic Match", sa[movie_id]))
            if movie_id in sb:
                raw.append(("auteur", "Director/Actor You Follow", sb[movie_id]))
            if movie_id in sc:
                raw.append(("crowd", "Hidden Gem Signal", sc[movie_id]))
            if not raw:
                return []
            total = sum(s for _, _, s in raw)
            return sorted([
                {"type": t, "label": l, "score": round(s / total, 3)}
                for t, l, s in raw
            ], key=lambda x: x["score"], reverse=True)
        """
        Final polish: RRF Score * Quality Score -> Collection Collapsing -> Batch Provider Fetch
        """
        if not rrf_scores:
            return []

        # 1. Batch-fetch Movie objects from DB (single query)
        movie_ids = list(rrf_scores.keys())
        stmt = select(Movie).where(Movie.id.in_(movie_ids))
        result = await self.db.execute(stmt)
        movies = result.scalars().all()

        # Minimum quality filter — defensive (Signal A pre-filters at the same
        # floor; Auteur and C use stricter floors). Any candidate that slipped in
        # via NULL vectorbox_score is dropped here.
        pre_quality = len(movies)
        movies = [m for m in movies if (m.vectorbox_score or 50) >= MIN_QUALITY_SCORE]
        logger.info(
            f"[Trident rerank] user={user_id} rrf_ids={len(movie_ids)} "
            f"pre_quality={pre_quality} post_quality={len(movies)}"
        )

        # 2. Score = RRF * Sigmoid Quality Weight
        candidates = []
        for m in movies:
            rrf_score = rrf_scores.get(m.id, 0)
            vb_score = m.vectorbox_score or 50
            quality_weight = self.clustering.calculate_quality_weight(vb_score)
            candidates.append({"movie": m, "movie_id": m.id, "score": rrf_score * quality_weight})

        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Director diversity cap — max 2 films per director in Picked For You
        MAX_PER_DIRECTOR = 2
        director_count: Dict[str, int] = {}
        director_capped = []
        for c in candidates:
            movie_directors = c["movie"].directors or []
            if any(director_count.get(d, 0) >= MAX_PER_DIRECTOR for d in movie_directors):
                continue
            director_capped.append(c)
            for d in movie_directors:
                director_count[d] = director_count.get(d, 0) + 1
        candidates = director_capped

        # 3. Batch-fetch Qdrant vectors for top 20 (single call, no N+1)
        import numpy as np
        import functools
        top_candidates = candidates[:20]
        candidate_tmdb_ids = [c["movie"].tmdb_id for c in top_candidates]
        raw_vectors = await self.qdrant.get_vectors_batch(candidate_tmdb_ids)
        # Map internal_id -> vector (get_vectors_batch returns tmdb_id -> vector)
        tmdb_to_internal = {c["movie"].tmdb_id: c["movie"].id for c in top_candidates}
        vectors_map = {
            tmdb_to_internal[tmdb_id]: np.array(v)
            for tmdb_id, v in raw_vectors.items()
            if tmdb_id in tmdb_to_internal
        }

        # 4. MMR Reranking (diversity-aware, CPU-bound → executor)
        # Filtrar candidatos que no tienen vector en Qdrant
        mmr_candidates_with_vectors = [
            c for c in top_candidates 
            if tmdb_to_internal.get(c["movie"].tmdb_id) in vectors_map
        ]

        logger.info(
            f"[Trident rerank] user={user_id} top_candidates={len(top_candidates)} "
            f"with_vectors={len(mmr_candidates_with_vectors)}"
        )
        if len(mmr_candidates_with_vectors) < 3:
            # No hay suficientes vectores, usar top-10 directo
            final_list = top_candidates[:10]
        else:
            loop = asyncio.get_running_loop()
            mmr_func = functools.partial(
                self.clustering.mmr_rerank,
                mmr_candidates_with_vectors,
                vectors_map,
                10,          # limit: 10 final items
                0.7          # lambda_param: 70% relevance, 30% diversity
            )
            try:
                final_list = await loop.run_in_executor(None, mmr_func)
            except Exception as e:
                logger.error(f"[Trident] MMR failed, falling back to top-10: {e}")
                # Fallback: simple collection collapsing
                seen_collections: set = set()
                final_list = []
                for c in candidates:
                    m = c["movie"]
                    if m.collection_id:
                        if m.collection_id in seen_collections:
                            continue
                        seen_collections.add(m.collection_id)
                    final_list.append(c)
                    if len(final_list) >= 10:
                        break

        # 5. Batch-fetch providers (single query, no N+1)
        feed_items = []
        if provider_service and final_list:
            movie_internal_ids = [item["movie"].id for item in final_list]
            providers_map = await provider_service.get_providers_batch(movie_internal_ids, country)
        else:
            providers_map = {}

        for item in final_list:
            movie = item["movie"]
            p_data = providers_map.get(movie.id, [])
            providers = [p["provider_name"] for p in p_data]
            feed_items.append(FeedItem(
                id=movie.tmdb_id,
                title=movie.title,
                poster_url=movie.poster_path,
                match_score=98,
                streaming_providers=list(set(providers)),
                year=movie.year,
                runtime=movie.runtime,
                letterboxd_uri=movie.letterboxd_uri,
                rating=movie.vote_average,
                overview=movie.overview,
                contributors=build_contributors(movie.id, signal_a_ids, signal_b_ids, signal_c_ids),
                vectorbox_score=movie.vectorbox_score,
                imdb_rating=movie.imdb_rating,
                metacritic_rating=movie.metacritic_rating,

                title_es=movie.title_es,
                overview_es=movie.overview_es
            ))

        return feed_items

    @safe_execution(fallback_return=FeedSection(id="auteur", title="From Your Favorite Directors", items=[]))
    async def get_auteur_section(self, user_id: int, country: str, seen_ids: Set[int], provider_service: ProviderService = None) -> FeedSection:
        """
        Signal Auteur row — top 3 directors by score × up to 3 unwatched films each (max 9).
        """
        director_scores = await self._compute_director_scores(user_id)
        top_directors = [
            name for name, score in sorted(director_scores.items(), key=lambda x: x[1], reverse=True)
            if score >= 2.0
        ][:3]

        if not top_directors:
            return FeedSection(id="auteur", title="From Your Favorite Directors", items=[])

        watched_result = await self.db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.is_watched.is_(True))
        )
        watched_internal_ids = set(watched_result.scalars().all())

        all_items: List[Tuple[Movie, str]] = []
        seen_local: Set[int] = set()
        directors_used: List[str] = []

        # Over-collect 7 per director: feed-level dedup (feed_service) may strip films
        # already shown in earlier sections (Picked For You, Because You Watched, etc.).
        # Post-trim happens in feed_service to keep <=3 per director × <=9 total.
        for director_name in top_directors:
            stmt = (
                select(Movie)
                .where(Movie.directors.any(director_name))
                .where(Movie.id.notin_(watched_internal_ids))
                .where(Movie.id.notin_(seen_local))
                .where(Movie.vectorbox_score >= 60)
                .where(Movie.vote_count >= 50)
                .where(Movie.year.isnot(None))
                .order_by(desc(Movie.vectorbox_score))
                .limit(12)
            )
            result = await self.db.execute(stmt)
            director_films = result.scalars().all()

            per_director = 0
            for movie in director_films:
                if per_director >= 7:
                    break
                if movie.tmdb_id in seen_ids:
                    continue
                all_items.append((movie, director_name))
                seen_local.add(movie.id)
                per_director += 1

            if per_director > 0:
                directors_used.append(director_name)

        # Progressive fallback: if fewer than 3 distinct directors had films, expand to directors 4-10
        if len(directors_used) < 3:
            extended_directors = [
                name for name, score in sorted(director_scores.items(), key=lambda x: x[1], reverse=True)
                if score >= 1.5 and name not in top_directors
            ][:7]

            for director_name in extended_directors:
                if len(all_items) >= 21:
                    break

                stmt = (
                    select(Movie)
                    .where(Movie.id.notin_(watched_internal_ids))
                    .where(Movie.tmdb_id.notin_(seen_ids))
                    .where(Movie.id.notin_(seen_local))
                    .where(Movie.directors.any(director_name))
                    .where(Movie.vectorbox_score >= 60)
                    .where(Movie.vote_count >= 50)
                    .where(Movie.year.isnot(None))
                    .order_by(desc(Movie.vectorbox_score))
                    .limit(7)
                )
                result = await self.db.execute(stmt)
                fallback_films = result.scalars().all()

                added = 0
                for movie in fallback_films:
                    if len(all_items) >= 21:
                        break
                    all_items.append((movie, director_name))
                    seen_local.add(movie.id)
                    added += 1

                if added > 0 and director_name not in directors_used:
                    directors_used.append(director_name)

        if not all_items:
            return FeedSection(id="auteur", title="From Your Favorite Directors", items=[])

        # FIX 3: Anti-vector — drop films too close to the user's negative profile,
        # even if the director matches. A user who has rejected several action films
        # should not see the action half of a director's filmography.
        anti_vector = await self._get_anti_vector(user_id)
        all_items, anti_dropped = await self._filter_by_anti_vector(all_items, anti_vector)
        if anti_dropped:
            logger.info(f"[Auteur anti-vector] user={user_id} dropped={anti_dropped}")

        if not all_items:
            return FeedSection(id="auteur", title="From Your Favorite Directors", items=[])

        unique_movies = all_items[:21]

        if len(directors_used) == 1:
            title = f"Because You Love {directors_used[0]}"
        elif len(directors_used) == 2:
            title = f"Because You Love {directors_used[0]} & {directors_used[1]}"
        elif len(directors_used) >= 3:
            title = f"Because You Love {directors_used[0]}, {directors_used[1]} & More"
        else:
            title = "From Your Favorite Directors"

        if provider_service and unique_movies:
            movie_ids = [m.id for m, _ in unique_movies]
            providers_map = await provider_service.get_providers_batch(movie_ids, country)
        else:
            providers_map = {}

        items = []
        for m, director_name in unique_movies:
            p_data = providers_map.get(m.id, [])
            flat_providers = [p["provider_name"] for p in p_data]
            items.append(FeedItem(
                id=m.tmdb_id,
                title=m.title,
                poster_url=m.poster_path,
                match_score=90,
                streaming_providers=flat_providers,
                year=m.year,
                runtime=m.runtime,
                overview=m.overview,
                vectorbox_score=m.vectorbox_score,
                contributors=[{
                    "type": "auteur",
                    "label": f"Director you follow: {director_name}",
                    "director": director_name,
                }],
            ))
            seen_ids.add(m.tmdb_id)

        return FeedSection(id="auteur", title=title, items=items)

    @safe_execution(fallback_return=FeedSection(id="cult_actor", title="Cast Picks", items=[]))
    async def get_cult_actor_section(self, user_id: int, country: str, seen_ids: Set[int], provider_service: ProviderService = None) -> FeedSection:
        """
        Imp 2: Cast-based auteur signal — "Because you follow {actor_name}"
        """
        from services.recommendation_engine import _director_weight

        # 1. Get rated/liked movies with cast data
        stmt = select(UserRating, Movie).join(Movie, UserRating.movie_id == Movie.id)\
            .where(
                UserRating.user_id == user_id,
                or_(
                    UserRating.rating >= 3.5,
                    UserRating.is_liked.is_(True)
                )
            )
        result = await self.db.execute(stmt)
        rated_movies = result.all()

        if not rated_movies:
            return FeedSection(id="cult_actor", title="Cast Picks", items=[])

        # 2. Build weighted frequency counter over first 3 billed actors
        # Recency decay (half-life 730 days) + saga penalty to prevent franchise actors
        # (e.g. Harry Potter cast) from dominating via accumulated appearances.
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        # Count appearances per actor first (for saga penalty)
        actor_appearances: Dict[str, int] = {}
        for _, movie in rated_movies:
            if not movie.cast:
                continue
            for actor in movie.cast[:3]:
                actor_appearances[actor] = actor_appearances.get(actor, 0) + 1

        actor_scores: Dict[str, float] = {}
        for rating_obj, movie in rated_movies:
            if not movie.cast:
                continue
            effective_rating = rating_obj.rating or 4.5  # liked = 4.5
            base_weight = _director_weight(effective_rating)
            if base_weight == 0:
                continue

            wd = rating_obj.watched_date
            if wd is not None:
                if wd.tzinfo is None:
                    wd = wd.replace(tzinfo=timezone.utc)
                days_ago = max(0, (now - wd).days)
            else:
                days_ago = 730
            decay = 0.5 ** (days_ago / 730)

            for actor in movie.cast[:3]:
                appearances = actor_appearances.get(actor, 1)
                saga_penalty = 1.0 / (1.0 + max(0, appearances - 3) * 0.3)
                final_weight = base_weight * decay * saga_penalty
                actor_scores[actor] = actor_scores.get(actor, 0) + final_weight

        if not actor_scores:
            return FeedSection(id="cult_actor", title="Cast Picks", items=[])

        # 3. Top 3 cult actors by weighted score (mirrors auteur)
        top_actors = sorted(actor_scores.items(), key=lambda x: x[1], reverse=True)[:3]

        # Get watched internal IDs
        watched_result = await self.db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.is_watched.is_(True))
        )
        watched_internal_ids = set(watched_result.scalars().all())

        all_items: List[Tuple[Movie, str]] = []
        seen_local: Set[int] = set()
        actors_used: List[str] = []

        # Section enforces <=3 per actor × <=9 total. limit(8) is a small over-fetch
        # so the per_actor cap can skip films already in seen_ids without exhausting
        # the candidate pool.
        for actor_name, _ in top_actors:
            stmt = (
                select(Movie)
                .where(Movie.cast.any(actor_name))
                .where(Movie.id.notin_(watched_internal_ids))
                .where(Movie.id.notin_(seen_local))
                .where(Movie.vectorbox_score >= 60)
                .where(Movie.vote_count >= 50)
                .where(Movie.year.isnot(None))
                .order_by(desc(Movie.vectorbox_score))
                .limit(8)
            )
            result = await self.db.execute(stmt)
            actor_films = result.scalars().all()

            per_actor = 0
            for movie in actor_films:
                if per_actor >= 3:
                    break
                if movie.tmdb_id in seen_ids:
                    continue
                all_items.append((movie, actor_name))
                seen_local.add(movie.id)
                per_actor += 1

            if per_actor > 0:
                actors_used.append(actor_name)

        # Progressive fallback: if total < 9 (any of the top 3 lacked 3 films),
        # pull from actors 4-10 to fill up to 9.
        if len(all_items) < 9:
            top_actor_names = {name for name, _ in top_actors}
            extended_actors = [
                name for name, score in sorted(actor_scores.items(), key=lambda x: x[1], reverse=True)
                if score >= 1.5 and name not in top_actor_names
            ][:7]

            for actor_name in extended_actors:
                if len(all_items) >= 9:
                    break

                stmt = (
                    select(Movie)
                    .where(Movie.cast.any(actor_name))
                    .where(Movie.id.notin_(watched_internal_ids))
                    .where(Movie.tmdb_id.notin_(seen_ids))
                    .where(Movie.id.notin_(seen_local))
                    .where(Movie.vectorbox_score >= 60)
                    .where(Movie.vote_count >= 50)
                    .where(Movie.year.isnot(None))
                    .order_by(desc(Movie.vectorbox_score))
                    .limit(5)
                )
                result = await self.db.execute(stmt)
                fallback_films = result.scalars().all()

                added = 0
                for movie in fallback_films:
                    if added >= 3 or len(all_items) >= 9:
                        break
                    all_items.append((movie, actor_name))
                    seen_local.add(movie.id)
                    added += 1

                if added > 0 and actor_name not in actors_used:
                    actors_used.append(actor_name)

        if not all_items:
            return FeedSection(id="cult_actor", title="Cast Picks", items=[])

        # FIX 3: Anti-vector — drop cast films too close to the user's negative
        # profile (action-heavy actors won't pull in action thrillers when rejected).
        anti_vector = await self._get_anti_vector(user_id)
        all_items, anti_dropped = await self._filter_by_anti_vector(all_items, anti_vector)
        if anti_dropped:
            logger.info(f"[Cult Actor anti-vector] user={user_id} dropped={anti_dropped}")

        if not all_items:
            return FeedSection(id="cult_actor", title="Cast Picks", items=[])

        unique_movies = all_items[:9]

        if provider_service and unique_movies:
            movie_ids = [m.id for m, _ in unique_movies]
            providers_map = await provider_service.get_providers_batch(movie_ids, country)
        else:
            providers_map = {}

        items = []
        for m, matched_actor in unique_movies:
            p_data = providers_map.get(m.id, [])
            flat_providers = [p["provider_name"] for p in p_data]
            items.append(FeedItem(
                id=m.tmdb_id,
                title=m.title,
                poster_url=m.poster_path,
                match_score=88,
                streaming_providers=flat_providers,
                year=m.year,
                runtime=m.runtime,
                overview=m.overview,
                vectorbox_score=m.vectorbox_score,
                contributors=[{
                    "type": "cult_actor",
                    "label": f"Actor you follow: {matched_actor}",
                    "actor": matched_actor,
                }],
            ))
            seen_ids.add(m.tmdb_id)

        return FeedSection(
            id="cult_actor",
            title="Cast Picks",
            items=items
        )

    async def close(self):
        """Cleanup resources"""
        if self.tmdb:
            await self.tmdb.aclose()
        if self.movie_service:
            await self.movie_service.close()
