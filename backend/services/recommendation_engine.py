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
from opentelemetry import trace
from telemetry import get_tracer

logger = logging.getLogger(__name__)
_tracer = get_tracer("recommendation_engine")


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
            "min_score": 75,
            "max_popularity": 20,
            "min_votes": 500,
        }

def _score_anchor_candidate(rating: float, watched_date: datetime, now: datetime) -> float:
    """
    Combine rating quality with recency decay.
    Decay half-life: 180 days (score halves every 6 months).
    """
    days_ago = max(0, (now - watched_date).days) if watched_date else 365
    decay = 0.5 ** (days_ago / 180)
    return (rating / 5.0) * decay


def _apply_exoticism_boost(score: float, original_language: str) -> float:
    """Boost non-English films by 15% in Hidden Gems section."""
    if original_language and original_language != "en":
        return min(score * 1.15, 1.0)
    return score


def _director_weight(rating: float) -> float:
    """Weighted point system for director/actor auteur activation."""
    if rating >= 4.5: return 2.0
    if rating >= 4.0: return 1.5
    if rating >= 3.5: return 0.8
    if rating >= 3.0: return 0.3
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
        Compute the average embedding vector of films the user rated <= 2 stars.
        Returns None if fewer than 3 such films exist (not enough signal).
        """
        # Step 1: Get internal movie_ids from UserRating
        rating_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id, UserRating.rating <= 2.0)
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
            .where(
                Movie.vectorbox_score.isnot(None),
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
        
        # Scale match score
        min_sim = 0.2
        max_sim = 0.7
        
        if score > max_sim:
            final_score = 90 + ((score - max_sim) * 100)
            final_score = min(99, final_score)
        else:
            normalized = (score - min_sim) / (max_sim - min_sim)
            normalized = max(0.0, min(1.0, normalized))
            final_score = 60 + (normalized * 30)
        
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
        background_tasks = None
    ) -> FeedSection:
        """Signal A: Because you watched [Movie X] — Item-Item Collaborative Filtering"""
        from constants import GENRE_CONTRADICTIONS
        with _tracer.start_as_current_span("trident.signal_a.because_you_watched") as span:
            span.set_attribute("user_id", user_id)
            span.set_attribute("country", country)

            # Imp 1: Fetch 20 candidates (not 5) for recency-weighted anchor selection
            result = await db.execute(
                select(UserRating, Movie)
                .join(Movie, UserRating.movie_id == Movie.id)
                .where(
                    UserRating.user_id == user_id,
                    or_(
                        UserRating.rating >= 4.0,
                        UserRating.is_liked.is_(True)
                    )
                )
                .order_by(
                    desc(func.coalesce(UserRating.watched_date, UserRating.created_at))
                )
                .limit(20)
            )
            
            candidates = result.all()
            if not candidates:
                span.set_attribute("result_count", 0)
                # Imp 9: Cold start fallback
                return await self._get_genre_fallback_section(user_id, db, tmdb, seen_ids, country, provider_service)
            
            # Imp 1: Score candidates with recency decay and pick best anchor
            now = datetime.utcnow()
            scored_candidates = []
            for row in candidates:
                user_rating, movie = row
                effective_rating = user_rating.rating or 4.5  # liked without rating
                watched_date = user_rating.watched_date or user_rating.created_at
                anchor_score = _score_anchor_candidate(effective_rating, watched_date, now)
                scored_candidates.append((anchor_score, user_rating, movie))
            
            scored_candidates.sort(key=lambda x: x[0], reverse=True)

            # Imp 5: Compute anti-vector once for this section
            anti_vector = await self._get_anti_vector(user_id, db, qdrant)
            anti_vector_np = np.array(anti_vector) if anti_vector else None

            for _, user_rating, anchor_movie in scored_candidates:
                
                if not self.embedding_service:
                    # No embedding service — fall back to stored vector
                    anchor_vector = await qdrant.get_vector(anchor_movie.tmdb_id)
                else:
                    if anchor_movie.keywords is None:
                        keywords = await tmdb.get_movie_keywords(anchor_movie.tmdb_id) or []
                    anchor_vector = self.embedding_service.generate_embedding({
                        "title": anchor_movie.title, 
                        "overview": anchor_movie.overview or "",
                        "genres": anchor_movie.genres or [],
                        "keywords": keywords
                    }, include_title=False).tolist()
                
                if not anchor_vector:
                     anchor_vector = await qdrant.get_vector(anchor_movie.tmdb_id)
                
                if not anchor_vector:
                     continue
                
                anchor_exclusions = set()
                for genre in (anchor_movie.genres or []):
                    anchor_exclusions.update(GENRE_CONTRADICTIONS.get(genre, []))
                anchor_exclusions -= set(anchor_movie.genres or [])

                similar_results = await qdrant.search_similar(
                    query_vector=anchor_vector,
                    limit=500,
                    score_threshold=0.1,
                    filters={"exclude_genres": list(anchor_exclusions)} if anchor_exclusions else None
                )
                
                found_tmdb_ids = [res["movie_id"] for res in similar_results]
                
                existing_movies_result = await db.execute(
                    select(Movie.tmdb_id).where(Movie.tmdb_id.in_(found_tmdb_ids))
                )
                existing_tmdb_ids = set(existing_movies_result.scalars().all())
                
                missing_ids = [mid for mid in found_tmdb_ids if mid not in existing_tmdb_ids]
                
                if missing_ids:
                    ids_to_ingest = missing_ids[:5]
                    movie_service = MovieService(db)
                    for mid in ids_to_ingest:
                        if background_tasks:
                             background_tasks.add_task(movie_service.get_or_create_movie, mid)
                        else:
                             try:
                                await movie_service.get_or_create_movie(mid)
                             except Exception as e:
                                logger.error(f"Failed to auto-ingest movie {mid}: {e}")
                
                target_ids = []
                for res in similar_results:
                    mid = res["movie_id"]
                    if mid not in seen_ids and mid != anchor_movie.tmdb_id:
                        target_ids.append(mid)
                
                target_ids = target_ids[:30] 
                
                if target_ids:
                    movies_result = await db.execute(
                        select(Movie).where(Movie.tmdb_id.in_(target_ids))
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
                        if len(mmr_candidates) >= 20:
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
                        mmr_candidates, vectors_map_mmr, 10, lambda_param=0.7
                    )
                    mmr_results = await loop.run_in_executor(None, mmr_func)
                except Exception as e:
                    logger.error(f"MMR failed in Signal A, falling back to top-10: {e}")
                    mmr_results = mmr_candidates[:10]

                items = []
                for cand in mmr_results:
                    movie = cand["movie"]
                    p_data = providers_map.get(movie.id, [])
                    s_providers = [p["provider_name"] for p in p_data]
                    
                    item = await self.create_feed_item(
                        movie, cand["score"], country, tmdb, 
                        provider_service=provider_service,
                        streaming_providers=s_providers
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

    async def get_your_taste_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None,
        background_tasks = None
    ) -> FeedSection:
        """Signal B: Your Taste: [Cluster Name] — Centroid Search"""
        from constants import GENRE_CONTRADICTIONS
        with _tracer.start_as_current_span("trident.signal_b.your_taste") as span:
            span.set_attribute("user_id", user_id)
            span.set_attribute("country", country)

            clusters_result = await db.execute(
                select(UserCluster)
                .where(UserCluster.user_id == user_id)
                .order_by(desc(UserCluster.movie_count))
            )
            clusters = clusters_result.scalars().all()
            
            if not clusters:
                span.set_attribute("result_count", 0)
                # Imp 9: Cold start fallback
                return await self._get_genre_fallback_section(user_id, db, tmdb, seen_ids, country, provider_service)
            
            cluster = clusters[0]
            
            cluster_exclusions = set()
            for genre in (cluster.dominant_genres or []):
                cluster_exclusions.update(GENRE_CONTRADICTIONS.get(genre, []))
            cluster_exclusions -= set(cluster.dominant_genres or [])

            filters_to_pass = {"exclude_genres": list(cluster_exclusions)} if cluster_exclusions else {}

            results = await self.clustering.get_cluster_recommendations(
                user_id=user_id,
                cluster_id=cluster.cluster_id,
                db=db,
                filters=filters_to_pass,
                limit=500,
                background_tasks=background_tasks
            )
            
            target_ids = [res["movie_id"] for res in results if res["movie_id"] not in seen_ids][:30]
            
            if target_ids:
                movies_result = await db.execute(
                    select(Movie).where(Movie.id.in_(target_ids))
                )
                fetched_movies = movies_result.scalars().all()
                movie_map = {m.id: m for m in fetched_movies}
                
                if provider_service:
                    valid_ids = [m.id for m in fetched_movies]
                    providers_map = await provider_service.get_providers_batch(valid_ids, country)
                else:
                    providers_map = {}
            else:
                movie_map = {}
                providers_map = {}

            # Imp 5: Compute anti-vector once for this section
            anti_vector = await self._get_anti_vector(user_id, db, self.qdrant)
            anti_vector_np = np.array(anti_vector) if anti_vector else None

            # Apply anti-vector penalty if available
            scores_map = {res["movie_id"]: res["score"] for res in results}
            if anti_vector_np is not None and target_ids:
                # Map internal IDs → tmdb_ids for Qdrant batch fetch
                internal_to_tmdb = {m.id: m.tmdb_id for m in movie_map.values()}
                tmdb_ids_for_anti = list(internal_to_tmdb.values())
                if tmdb_ids_for_anti:
                    candidate_vectors = await self.qdrant.get_vectors_batch(tmdb_ids_for_anti)
                    tmdb_to_internal = {v: k for k, v in internal_to_tmdb.items()}
                    for tid, vec in candidate_vectors.items():
                        vec_np = np.array(vec)
                        norm_product = np.linalg.norm(vec_np) * np.linalg.norm(anti_vector_np)
                        if norm_product > 0:
                            cos_sim = np.dot(vec_np, anti_vector_np) / norm_product
                            internal_id = tmdb_to_internal.get(tid)
                            if internal_id and internal_id in scores_map:
                                if cos_sim > 0.80:
                                    scores_map[internal_id] = scores_map[internal_id] * 0.3
                                elif cos_sim > 0.65:
                                    scores_map[internal_id] = scores_map[internal_id] * 0.6

            # Build intermediate candidate dicts for MMR (Imp 6)
            mmr_candidates = []
            for res in results:
                movie_id = res["movie_id"]
                if movie_id in seen_ids:
                    continue
                
                movie = movie_map.get(movie_id)
                if movie:
                    penalized_score = scores_map.get(movie_id, res["score"])
                    mmr_candidates.append({
                        "movie_id": movie.id,  # internal ID for MMR
                        "tmdb_id": movie.tmdb_id,
                        "score": penalized_score,
                        "movie": movie,
                    })
                    if len(mmr_candidates) >= 20:
                        break

            # Imp 6: Apply MMR reranking
            if mmr_candidates:
                try:
                    mmr_tmdb_ids = [c["tmdb_id"] for c in mmr_candidates]
                    mmr_vectors_raw = await self.qdrant.get_vectors_batch(mmr_tmdb_ids)
                    tmdb_to_internal = {c["tmdb_id"]: c["movie_id"] for c in mmr_candidates}
                    vectors_map_mmr = {
                        tmdb_to_internal[tid]: np.array(v)
                        for tid, v in mmr_vectors_raw.items()
                        if tid in tmdb_to_internal
                    }
                    
                    loop = asyncio.get_running_loop()
                    mmr_func = functools.partial(
                        self.clustering.mmr_rerank,
                        mmr_candidates, vectors_map_mmr, 10, lambda_param=0.7
                    )
                    mmr_results = await loop.run_in_executor(None, mmr_func)
                except Exception as e:
                    logger.error(f"MMR failed in Your Taste, falling back to top-10: {e}")
                    mmr_results = mmr_candidates[:10]
            else:
                mmr_results = []

            items = []
            for cand in mmr_results:
                movie = cand["movie"]
                p_data = providers_map.get(movie.id, [])
                s_providers = [p["provider_name"] for p in p_data]
                
                item = await self.create_feed_item(
                    movie, cand["score"], country, tmdb, 
                    provider_service=provider_service,
                    streaming_providers=s_providers
                )
                items.append(item)
                seen_ids.add(movie.id)  # seen_ids uses internal IDs for this section
            
            title = cluster.cluster_label or "Your Taste"

            span.set_attribute("result_count", len(items))
            span.set_attribute("cluster_id", cluster.cluster_id)
            return FeedSection(
                id="your_taste",
                title=title,
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
            span.set_attribute("country", country)

            # Dynamic thresholds based on user's movie count
            user_count_result = await db.execute(
                select(func.count(UserRating.id))
                .where(UserRating.user_id == user_id)
                .where(
                    or_(
                        UserRating.rating.isnot(None),
                        UserRating.is_liked.is_(True)
                    )
                )
            )
            user_movie_count = user_count_result.scalar() or 0
            thresholds = _get_signal_c_thresholds(user_movie_count)
            logger.info(f"[Signal C] User {user_id} has {user_movie_count} movies, using thresholds: {thresholds}")

            results = await self.clustering.get_user_centric_recommendations(
                user_id=user_id,
                db=db,
                filters={
                    "min_vectorbox_score": thresholds["min_score"],
                    "max_popularity": thresholds["max_popularity"],
                    "min_vote_count": thresholds["min_votes"]
                },
                limit=500,
                background_tasks=background_tasks
            )
            
            target_ids = [res["movie_id"] for res in results if res["movie_id"] not in seen_ids][:50]
            
            if target_ids:
                 movies_result = await db.execute(select(Movie).where(Movie.id.in_(target_ids)))
                 fetched_movies = movies_result.scalars().all()
                 movie_map = {m.id: m for m in fetched_movies}
                 
                 if provider_service:
                     valid_ids = [m.id for m in fetched_movies]
                     providers_map = await provider_service.get_providers_batch(valid_ids, country)
                 else:
                     providers_map = {}
            else:
                 movie_map = {}
                 providers_map = {}

            # Build intermediate candidate dicts with exoticism boost (Imp 4)
            mmr_candidates = []
            for res in results:
                movie_id = res["movie_id"]
                if movie_id in seen_ids:
                    continue
                
                movie = movie_map.get(movie_id)
                if movie and movie.vectorbox_score and movie.vectorbox_score > thresholds["min_score"]:
                    # Imp 4: Apply exoticism boost to non-English films
                    boosted_score = _apply_exoticism_boost(res["score"], movie.original_language)
                    mmr_candidates.append({
                        "movie_id": movie.id,  # internal ID for MMR
                        "tmdb_id": movie.tmdb_id,
                        "score": boosted_score,
                        "movie": movie,
                    })
                    if len(mmr_candidates) >= 20:
                        break

            # Imp 4: Re-sort by boosted score before MMR
            mmr_candidates.sort(key=lambda x: x["score"], reverse=True)

            # Imp 6: Apply MMR reranking
            if mmr_candidates:
                try:
                    mmr_tmdb_ids = [c["tmdb_id"] for c in mmr_candidates]
                    mmr_vectors_raw = await self.qdrant.get_vectors_batch(mmr_tmdb_ids)
                    tmdb_to_internal = {c["tmdb_id"]: c["movie_id"] for c in mmr_candidates}
                    vectors_map_mmr = {
                        tmdb_to_internal[tid]: np.array(v)
                        for tid, v in mmr_vectors_raw.items()
                        if tid in tmdb_to_internal
                    }
                    
                    loop = asyncio.get_running_loop()
                    mmr_func = functools.partial(
                        self.clustering.mmr_rerank,
                        mmr_candidates, vectors_map_mmr, 10, lambda_param=0.7
                    )
                    mmr_results = await loop.run_in_executor(None, mmr_func)
                except Exception as e:
                    logger.error(f"MMR failed in Hidden Gems, falling back to top-10: {e}")
                    mmr_results = mmr_candidates[:10]
            else:
                mmr_results = []

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
                seen_ids.add(movie_id)
            
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
                        overview=movie.overview
                    ))
                    seen_ids.add(movie.tmdb_id)
                    if len(items) >= 20:
                        break
        
        return FeedSection(
            id="available_now",
            title="Available on Your Services",
            items=items
        )

    async def get_deep_dive_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None,
        include_low_quality: bool = False,
        background_tasks = None
    ) -> FeedSection:
        """Section: Deep Dive"""
        results = await self.clustering.get_item_based_recommendations(
            user_id=user_id,
            db=db,
            limit=60,
            include_low_quality=include_low_quality,
            background_tasks=background_tasks
        )
        
        target_ids = [res["movie_id"] for res in results if res["movie_id"] not in seen_ids][:30]
        
        if target_ids:
            movies_result = await db.execute(select(Movie).where(Movie.id.in_(target_ids)))
            fetched_movies = movies_result.scalars().all()
            movie_map = {m.id: m for m in fetched_movies}
            if provider_service:
                valid_ids = [m.id for m in fetched_movies]
                providers_map = await provider_service.get_providers_batch(valid_ids, country)
            else:
                providers_map = {}
        else:
            movie_map = {}
            providers_map = {}
        
        items = []
        for res in results:
            movie_id = res["movie_id"]
            if movie_id in seen_ids:
                continue
            movie = movie_map.get(movie_id)
            if movie:
                # Imp 7: Trust Bucket — skip obscure films with low similarity
                is_obscure = (movie.vote_count or 0) < 5000
                if is_obscure and res["score"] < 0.85:
                    logger.debug(
                        f"Trust Bucket: obscure film {movie.title} score {res['score']:.2f} < 0.85, skipping from Deep Dive"
                    )
                    continue

                p_data = providers_map.get(movie.id, [])
                s_providers = [p["provider_name"] for p in p_data]
                contributors = res.get("contributors", [])
                item = await self.create_feed_item(
                    movie, res["score"], country, tmdb, 
                    contributors=contributors, 
                    provider_service=provider_service,
                    streaming_providers=s_providers
                )
                items.append(item)
                seen_ids.add(movie_id)
                if len(items) >= 10:
                    break
                    
        return FeedSection(id="deep_dive", title="Deep Dive", items=items)

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

        result = await db.execute(
            select(Movie)
            .where(Movie.vectorbox_score.isnot(None))
            .where(Movie.vote_average > 7.0)
            .where(Movie.vote_count > 100)
            .order_by(desc(Movie.vectorbox_score))
            .limit(1000)
        )
        candidates = result.scalars().all()
        
        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
        )
        watched_ids = set(watched_result.scalars().all())
        
        wildcard_candidates = []
        for m in candidates:
            if m.tmdb_id in seen_ids or m.id in watched_ids:
                continue
            movie_genres = set(m.genres or [])
            if movie_genres.isdisjoint(excluded_genres):
                wildcard_candidates.append(m)
        
        if not wildcard_candidates:
            return None
            
        sample = random.sample(wildcard_candidates, min(10, len(wildcard_candidates)))
        
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
        result = await db.execute(
            select(Movie)
            .where(Movie.vectorbox_score.isnot(None))
            .order_by(desc(Movie.vectorbox_score))
            .limit(500)
        )
        candidates = result.scalars().all()
        
        if not candidates:
            result = await db.execute(
                select(Movie)
                .where(Movie.vote_average > 7.0)
                .order_by(desc(Movie.vote_average))
                .limit(500)
            )
            candidates = result.scalars().all()
        
        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
        )
        watched_ids = set(watched_result.scalars().all())
        
        unseen_candidates = [m for m in candidates if m.tmdb_id not in seen_ids and m.id not in watched_ids]
        
        if not unseen_candidates:
            return None
            
        sample = random.sample(unseen_candidates, min(10, len(unseen_candidates)))
        
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
        popular_ids = await trending_service.get_popular_movie_ids()
        
        if not popular_ids:
            return None
            
        result = await db.execute(
            select(Movie).where(Movie.tmdb_id.in_(popular_ids))
        )
        fetched_movies = result.scalars().all()
        movies_map = {m.tmdb_id: m for m in fetched_movies}

        # Batch-fetch providers (no N+1)
        if provider_service and fetched_movies:
            internal_ids = [m.id for m in fetched_movies]
            providers_map = await provider_service.get_providers_batch(internal_ids, country)
        else:
            providers_map = {}
        
        items = []
        for tmdb_id in popular_ids:
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
        )
        candidates = result.scalars().all()
        
        if not candidates:
            return None
            
        selected_movies = random.sample(candidates, min(20, len(candidates)))

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
