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
        
        movie_ids = [r["movie_id"] for r in raw_recs if r["movie_id"] not in exclude_ids]
        
        if not movie_ids:
            return []
            
        # Fetch Movie objects
        stmt = select(Movie).where(Movie.id.in_(movie_ids))
        result = await self.db.execute(stmt)
        movies = result.scalars().all()
        
        # Re-sort match per raw_recs order
        movies_map = {m.id: m for m in movies}
        ordered = []
        for mid in movie_ids:
            if mid in movies_map:
                ordered.append(movies_map[mid])
                
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

    async def _compute_auteur_signal_raw(self, user_id: int, exclude_ids: Set[int]) -> List[Movie]:
        """
        Raw computation for Signal Auteur: The Auteur Expert (Metadata Graph)
        Imp 8: Uses weighted point system instead of hard count threshold.
        """
        from services.recommendation_engine import _director_weight

        # 1. Analyze Top Directors with weighted scoring
        # Get rated/liked movies (lowered threshold to 3.0 for weighted system)
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
            return []

        # Build weighted director scores
        director_scores: Dict[str, float] = {}
        for rating_obj, movie in rated_movies:
            if not movie.directors:
                continue
            effective_rating = rating_obj.rating or 4.5  # liked = 4.5
            weight = _director_weight(effective_rating)
            if weight > 0:
                for director in movie.directors:
                    director_scores[director] = director_scores.get(director, 0) + weight

        # Imp 8: Director activates at >= 3.0 points
        top_directors = [name for name, score in sorted(director_scores.items(), key=lambda x: x[1], reverse=True) if score >= 3.0][:5]
        
        if not top_directors:
            return []
            
        # 2. Query DB for matches
        stmt = select(Movie).where(
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
        for m in candidates:
            if m.tmdb_id in exclude_ids or m.id in watched_ids:
                continue
            final_list.append(m)
            
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
        
        # 1. Get 3 most recent 5-star movies
        stmt = select(Movie).join(UserRating, Movie.id == UserRating.movie_id)\
            .where(UserRating.user_id == user_id, UserRating.rating == 5.0)\
            .order_by(desc(UserRating.watched_date))\
            .limit(3)
            
        seeds = (await self.db.execute(stmt)).scalars().all()
        
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

        # 5. Dynamic quality threshold based on profile richness.
        # Mirrors Hidden Gems: rich profiles (>=100 watched) deserve stricter floor (70).
        watch_count_stmt = select(func.count()).select_from(UserRating).where(
            UserRating.user_id == user_id,
            UserRating.is_watched.is_(True),
        )
        user_watch_count = (await self.db.execute(watch_count_stmt)).scalar() or 0
        signal_c_min_score = 70 if user_watch_count >= 100 else 55

        # 6. Filter and deduplicate
        seen_local: Set[int] = set()
        unique: List[Movie] = []
        for m in existing_movies:
            if m.id in seen_local or m.tmdb_id in exclude_ids:
                continue
            if (m.vectorbox_score or 0) < signal_c_min_score:
                continue
            unique.append(m)
            seen_local.add(m.id)

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

        # Minimum quality filter — no movie below 55 VB score in Picked For You
        MIN_QUALITY_SCORE = 55
        movies = [m for m in movies if (m.vectorbox_score or 50) >= MIN_QUALITY_SCORE]

        # 2. Score = RRF * Sigmoid Quality Weight
        candidates = []
        for m in movies:
            rrf_score = rrf_scores.get(m.id, 0)
            vb_score = m.vectorbox_score or 50
            quality_weight = self.clustering.calculate_quality_weight(vb_score)
            candidates.append({"movie": m, "movie_id": m.id, "score": rrf_score * quality_weight})

        candidates.sort(key=lambda x: x["score"], reverse=True)

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
                rotten_tomatoes_rating=movie.rotten_tomatoes_rating,
                title_es=movie.title_es,
                overview_es=movie.overview_es
            ))

        return feed_items

    @safe_execution(fallback_return=FeedSection(id="auteur_picks", title="From Your Favorite Directors", items=[]))
    async def get_auteur_section(self, user_id: int, country: str, seen_ids: Set[int], provider_service: ProviderService = None) -> FeedSection:
        """
        Signal Auteur Only Row: "From Your Favorite Directors"
        """
        candidates = await self.get_signal_b_auteur(user_id, seen_ids)
        
        # Batch fetch providers (no N+1)
        if provider_service and candidates:
            candidate_ids = [m.id for m in candidates]
            providers_map = await provider_service.get_providers_batch(
                candidate_ids, country
            )
        else:
            providers_map = {}
        
        items = []
        for m in candidates:
             p_data = providers_map.get(m.id, [])
             flat_providers = [p["provider_name"] for p in p_data]
             director_name = (m.directors or [None])[0]
             auteur_contrib = {
                "type": "auteur",
                "label": f"Director you follow: {director_name}" if director_name else "Director you follow",
             }
             if director_name:
                 auteur_contrib["director"] = director_name

             items.append(FeedItem(
                id=m.tmdb_id,
                title=m.title,
                poster_url=m.poster_path,
                match_score=90,
                streaming_providers=flat_providers,
                year=m.year,
                overview=m.overview,
                vectorbox_score=m.vectorbox_score,
                contributors=[auteur_contrib],
             ))
             seen_ids.add(m.tmdb_id)
             
        return FeedSection(
            id="auteur_picks",
            title="From Your Favorite Directors",
            items=items
        )

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
        actor_scores: Dict[str, float] = {}
        for rating_obj, movie in rated_movies:
            if not movie.cast:
                continue
            effective_rating = rating_obj.rating or 4.5  # liked = 4.5
            weight = _director_weight(effective_rating)
            if weight > 0:
                for actor in movie.cast[:3]:  # First 3 billed only
                    actor_scores[actor] = actor_scores.get(actor, 0) + weight

        # Threshold: actor must have >= 2.5 points
        qualifying_actors = [
            (name, score) for name, score in sorted(actor_scores.items(), key=lambda x: x[1], reverse=True)
            if score >= 2.5
        ]

        if not qualifying_actors:
            return FeedSection(id="cult_actor", title="Cast Picks", items=[])

        # 3. Top 2 cult actors
        top_actors = qualifying_actors[:2]
        actor_name = top_actors[0][0]  # Use top actor for title

        # Get watched IDs
        watched_stmt = select(UserRating.movie_id).where(
            UserRating.user_id == user_id, UserRating.is_watched.is_(True)
        )
        watched_ids = set((await self.db.execute(watched_stmt)).scalars().all())

        # 4. Find their unwatched films
        actor_names = [a[0] for a in top_actors]
        all_items = []

        for actor in actor_names:
            # Query movies where cast contains this actor, not watched
            stmt = select(Movie).where(
                Movie.cast.any(actor),
                Movie.vectorbox_score.isnot(None)
            ).order_by(desc(Movie.vectorbox_score)).limit(15)

            candidates = (await self.db.execute(stmt)).scalars().all()

            for m in candidates:
                if m.tmdb_id in seen_ids or m.id in watched_ids:
                    continue
                all_items.append((m, actor))

        # Deduplicate (keep the first actor match for each movie)
        seen_local: Set[int] = set()
        unique_movies: List[Tuple[Movie, str]] = []
        for m, actor in all_items:
            if m.id not in seen_local:
                unique_movies.append((m, actor))
                seen_local.add(m.id)

        unique_movies = unique_movies[:15]

        # Batch fetch providers
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
            title=f"Because you follow {actor_name}",
            items=items
        )

    async def close(self):
        """Cleanup resources"""
        if self.tmdb:
            await self.tmdb.aclose()
        if self.movie_service:
            await self.movie_service.close()
