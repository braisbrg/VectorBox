"""
K-Means Clustering Service for User Taste Profiles
Implements dynamic clustering: n_clusters = min(5, max(2, total_movies // 20))
"""
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from collections import Counter
import numpy as np
import math
from typing import List, Dict, Tuple, Optional
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, desc, func
from fastapi_cache.decorator import cache
import asyncio
import time
import functools

from models.database import UserRating, Movie, UserCluster
from services.qdrant_service import QdrantService

logger = logging.getLogger(__name__)

from config import AsyncSessionLocal

async def _enrich_movie_background(tmdb_id: int):
    """Background-safe enrichment: creates its own DB session."""
    from services.movie_service import MovieService
    from models.database import Movie
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Movie).where(Movie.tmdb_id == tmdb_id))
        movie = result.scalar_one_or_none()
        if movie:
            movie_service = MovieService(session)
            await movie_service.enrich_movie(movie)

class ClusteringService:
    """Create and manage user taste clusters"""
    
    def __init__(self, qdrant: QdrantService = None):
        self.qdrant = qdrant or QdrantService()
    
    @staticmethod
    def calculate_optimal_clusters(n_movies: int) -> int:
        """
        Calculate optimal number of clusters based on user's movie count
        Formula: n_clusters = min(5, max(2, total_movies // 20))
        """
        n_clusters = max(2, n_movies // 20)
        n_clusters = min(5, n_clusters)
        return n_clusters

    @staticmethod
    def _compute_medoid(vectors: np.ndarray) -> tuple[np.ndarray, int]:
        """
        Find the vector in `vectors` closest to the cluster centroid.
        Returns (medoid_vector, medoid_index).
        """
        centroid = np.mean(vectors, axis=0)
        distances = np.linalg.norm(vectors - centroid, axis=1)
        medoid_idx = int(np.argmin(distances))
        return vectors[medoid_idx], medoid_idx

    @staticmethod
    async def generate_cluster_label(
        sample_film_titles: list[str],
        dominant_genres: list[str],
        groq_client,
    ) -> str:
        """
        Generate a cinematic cluster label using Groq.
        Falls back to genre-based label on failure.
        """
        fallback = ", ".join(dominant_genres[:2]) if dominant_genres else "Cinema"

        if groq_client is None:
            return fallback

        titles_str = ", ".join(sample_film_titles[:5])
        genres_str = ", ".join(dominant_genres[:3]) if dominant_genres else "Various"

        prompt = (
            f"Films: {titles_str}\n"
            f"Dominant genres: {genres_str}\n\n"
            "Generate a cinematic cluster name of 2-4 words maximum. "
            "Style examples: 'Slow Burn Noir', 'European Art House', "
            "'80s Synth Sci-Fi', 'Korean Revenge Cinema', 'Quiet Contemplative Drama'. "
            "Respond with ONLY the label, no explanation, no punctuation at the end."
        )

        try:
            response = await groq_client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": "You name film clusters. Respond with ONLY the label in English. 2-4 words. No punctuation at the end.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_tokens=20,
            )
            label = response.choices[0].message.content.strip().rstrip(".!,;:")
            if label and 1 < len(label) < 60:
                logger.info(f"LLM cluster label generated: '{label}'")
                return label
            
            logger.warning(f"LLM cluster label fell back to genres (invalid response: '{label}')")
            return fallback
        except Exception as e:
            logger.warning(f"LLM cluster label fell back to genres (Groq error: {e})")
            return fallback
    
    async def create_user_clusters(
        self,
        user_id: int,
        db: AsyncSession,
        use_recency_bias: bool = False,
        background_tasks = None,
        groq_client = None
    ) -> List[UserCluster]:
        """
        Generate K-Means clusters for a user's taste profile
        Returns list of UserCluster objects
        """
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.rating.isnot(None), UserRating.is_liked.is_(True)))
        )
        ratings_movies = result.all()
        
        ratings_movies.sort(key=lambda x: x[1].id)
        
        if len(ratings_movies) < 10:
            if len(ratings_movies) < 5:
                logger.warning(f"User {user_id} has too few rated/liked movies ({len(ratings_movies)}) for clustering")
                return []
        
        # FIX: Never block the feed path with inline enrichment.
        # Vectors already exist in Qdrant at ingest time — keywords are optional metadata.
        # Only schedule as background task if context is available.
        from services.movie_service import MovieService
        movie_service = MovieService(db)
        
        for _, movie in ratings_movies:
            if movie.keywords is None and background_tasks:
                background_tasks.add_task(_enrich_movie_background, movie.tmdb_id)
            # If no background_tasks: skip silently. Vector exists, enrichment is cosmetic here.

        # Retrieve vectors from Qdrant (batch, no N+1)
        movie_tmdb_ids = [movie.tmdb_id for _, movie in ratings_movies]
        raw_vectors_map = await self.qdrant.get_vectors_batch(movie_tmdb_ids)

        vectors = []
        ratings = []
        movies_data = []

        for rating, movie in ratings_movies:
            vector = raw_vectors_map.get(movie.tmdb_id)
            if vector is not None:
                effective_rating = rating.rating
                if effective_rating is None and rating.is_liked:
                    effective_rating = 4.5
                
                if effective_rating is None:
                    continue

                vectors.append(vector)
                ratings.append(effective_rating)
                movies_data.append({
                    "id": movie.id,
                    "tmdb_id": movie.tmdb_id,
                    "title": movie.title,
                    "year": movie.year,
                    "genres": movie.genres or [],
                    "rating": effective_rating
                })
        
        if len(vectors) < 5:
            logger.warning("Insufficient movie vectors for clustering (Minimum 5 required)")
            return []
        
        X = np.array(vectors)
        
        if X.shape[1] != 384:
            logger.error(f"Vector Dimension Mismatch! Expected 384, got {X.shape[1]}. Aborting clustering.")
            return []
        
        from sklearn.preprocessing import normalize
        try:
            X_normalized = normalize(X)
        except Exception as e:
            logger.error(f"Normalization failed: {e}")
            return []
        
        ratings_array = np.array(ratings)
        
        weights = []
        now = datetime.now(timezone.utc)
        
        for i, r in enumerate(ratings):
            if r >= 4.5:
                w = 1.0
            elif r >= 4.0:
                w = 0.85
            elif r >= 3.5:
                w = 0.65
            elif r >= 3.0:
                w = 0.35
            elif r >= 2.0:
                w = 0.15
            else:
                w = 0.05

            rating_obj = ratings_movies[i][0]

            if use_recency_bias:
                date = rating_obj.watched_date or rating_obj.created_at

                if date:
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    age_days = max(0, (now - date).days)
                    decay = max(0.6, 0.5 ** (age_days / 730))
                    w *= decay

            # Rewatch boost: movies seen multiple times signal stronger preference
            watch_count = getattr(rating_obj, 'watch_count', 1) or 1
            if watch_count >= 3:
                w = min(w * 1.5, 1.0)
            elif watch_count == 2:
                w = min(w * 1.2, 1.0)

            weights.append(w)
            
        weights_array = np.array(weights)
        try:
             X_weighted = X_normalized * weights_array[:, np.newaxis]
        except Exception as e:
              logger.error(f"Weight application failed: {e}")
              return []
        
        n_clusters = self.calculate_optimal_clusters(len(vectors))
        
        kmeans = KMeans(
            n_clusters=n_clusters,
            random_state=42,
            n_init=10,
            max_iter=300
        )
        
        loop = asyncio.get_running_loop()
        start_time = time.perf_counter()
        
        cluster_labels = await loop.run_in_executor(
            None,
            kmeans.fit_predict, 
            X_weighted
        )
        
        duration = time.perf_counter() - start_time
        logger.info(f"K-Means clustering for user {user_id} took {duration:.4f}s")
        
        cluster_objects = []
        
        from sqlalchemy import delete
        await db.execute(delete(UserCluster).where(UserCluster.user_id == user_id))
        
        # Part A: Compute globally dominant genres (appear in >50% of all movies)
        total_movies = len(movies_data)
        global_genre_movie_count: dict[str, int] = {}
        for m in movies_data:
            for g in set(m["genres"]):  # set() to count once per movie
                global_genre_movie_count[g] = global_genre_movie_count.get(g, 0) + 1
        
        globally_dominant = {
            g for g, count in global_genre_movie_count.items()
            if count / total_movies > 0.50
        }
        if globally_dominant:
            logger.info(f"User {user_id}: globally dominant genres (>50%): {globally_dominant}")
        
        for cluster_id in range(n_clusters):
            cluster_indices = np.where(cluster_labels == cluster_id)[0]
            cluster_movies = [movies_data[i] for i in cluster_indices]
            
            if not cluster_movies:
                continue
            
            if len(cluster_movies) < 5:
                continue
                
            avg_rating = np.mean([m["rating"] for m in cluster_movies])
            
            all_genres = []
            for m in cluster_movies:
                all_genres.extend(m["genres"])
            
            genre_counts = Counter(all_genres)
            # Filter out globally dominant genres from per-cluster labels
            filtered_genres = [g for g, c in genre_counts.most_common(6) if g not in globally_dominant]
            # Fall back to unfiltered if filtering removes everything
            dominant_genres = filtered_genres[:3] if filtered_genres else [g for g, c in genre_counts.most_common(3)]
            
            years = [m["year"] for m in cluster_movies if m["year"]]
            if years:
                avg_year = int(np.mean(years))
                decade = f"{(avg_year // 10) * 10}s"
            else:
                decade = "Various"
            
            # Compute medoid: find the real film closest to the cluster center
            cluster_vectors = X_normalized[cluster_indices]
            _, medoid_local_idx = self._compute_medoid(cluster_vectors)
            medoid_movie_data = cluster_movies[medoid_local_idx]
            medoid_movie_id = medoid_movie_data["id"]

            # Generate cluster label via LLM or fall back to genre-based
            cluster_movies_sorted = sorted(cluster_movies, key=lambda x: x["rating"], reverse=True)
            sample_titles = [m["title"] for m in cluster_movies_sorted[:5]]
            sample_movie_ids = [m["id"] for m in cluster_movies_sorted[:5]]

            label = await self.generate_cluster_label(
                sample_film_titles=sample_titles,
                dominant_genres=dominant_genres,
                groq_client=groq_client,
            )
            
            user_cluster = UserCluster(
                user_id=user_id,
                cluster_id=cluster_id,
                cluster_label=label,
                movie_count=len(cluster_movies),
                avg_rating=float(avg_rating),
                dominant_genres=dominant_genres,
                sample_movie_ids=sample_movie_ids,
                medoid_movie_id=medoid_movie_id
            )
            
            db.add(user_cluster)
            cluster_objects.append(user_cluster)
        
        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(f"DB commit failed during user cluster creation: {e}")
            raise
        
        return cluster_objects
    
    @cache(expire=3600)
    async def get_cluster_recommendations(
        self,
        user_id: int,
        cluster_id: int,
        db: AsyncSession,
        filters: Dict = None,
        limit: int = 20,
        page: int = 1,
        background_tasks = None,
        query_vector_override: list[float] = None
    ) -> List[Dict]:
        """
        Get movie recommendations for a specific cluster (mood)
        or use a custom query vector override (e.g. Profile Summary).
        """
        offset = (page - 1) * limit
        
        cluster_center = query_vector_override

        if cluster_center is None:
            result = await db.execute(
                select(UserCluster)
                .where(UserCluster.user_id == user_id)
                .where(UserCluster.cluster_id == cluster_id)
            )
            cluster = result.scalar_one_or_none()
            
            if not cluster:
                raise ValueError(f"Cluster {cluster_id} not found for user {user_id}")
            
            # Medoid-first: use the real film closest to the cluster center
            if cluster.medoid_movie_id:
                # Map medoid internal ID → tmdb_id
                medoid_result = await db.execute(
                    select(Movie.tmdb_id).where(Movie.id == cluster.medoid_movie_id)
                )
                medoid_tmdb_id = medoid_result.scalar_one_or_none()
                if medoid_tmdb_id:
                    medoid_vector = await self.qdrant.get_vector(medoid_tmdb_id)
                    if medoid_vector:
                        cluster_center = medoid_vector if isinstance(medoid_vector, list) else medoid_vector.tolist()
                    else:
                        logger.debug(f"Medoid vector not found in Qdrant for tmdb_id={medoid_tmdb_id}, falling back to centroid")
                else:
                    logger.debug(f"Medoid movie_id={cluster.medoid_movie_id} not found in DB, falling back to centroid")
            else:
                logger.debug(f"No medoid_movie_id set for cluster {cluster_id}, falling back to centroid")
            
            # Fallback to centroid if medoid is unavailable
            if cluster_center is None:
                result = await db.execute(
                    select(UserRating, Movie)
                    .join(Movie, UserRating.movie_id == Movie.id)
                    .where(UserRating.user_id == user_id)
                    .where(or_(UserRating.rating.isnot(None), UserRating.is_liked.is_(True)))
                )
                all_ratings = result.all()
                
                sample_ids = cluster.sample_movie_ids or []
                sample_tmdb_ids = [movie.tmdb_id for _, movie in all_ratings if movie.id in sample_ids]
                vectors_map = await self.qdrant.get_vectors_batch(sample_tmdb_ids)
                cluster_vectors = list(vectors_map.values())
                
                if not cluster_vectors:
                    raise ValueError("No vectors found for cluster samples")
                
                cluster_center = np.mean(cluster_vectors, axis=0).tolist()
        
        search_filters = filters or {}
        effective_threshold = 0.15 if query_vector_override is not None else 0.3

        results = await self.qdrant.search_similar(
            query_vector=cluster_center,
            limit=limit * 5,
            offset=offset,
            score_threshold=effective_threshold,
            filters=search_filters
        )
        
        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.is_watched.is_(True))
        )
        watched_movie_ids = set(watched_result.scalars().all())
        
        recommendations = [
            r for r in results
            if r["movie_id"] not in watched_movie_ids
        ][:limit]
        
        return recommendations

    @cache(expire=3600)
    async def get_user_centric_recommendations(
        self,
        user_id: int,
        db: AsyncSession,
        filters: Dict = None,
        limit: int = 20,
        page: int = 1,
        background_tasks = None
    ) -> List[Dict]:
        """
        Get general movie recommendations based on user's global taste profile (Clustering/Centroid).
        Used for "Hidden Gems" and general discovery.
        """
        offset = (page - 1) * limit
        
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.rating.isnot(None), UserRating.is_liked.is_(True)))
        )
        all_ratings = result.all()
        
        # FIX: Never block path with inline enrichment — background only.
        from services.movie_service import MovieService
        movie_service = MovieService(db)
        for _, movie in all_ratings:
            if movie.keywords is None and background_tasks:
                background_tasks.add_task(_enrich_movie_background, movie.tmdb_id)
        
        if not all_ratings:
            return []
        
        movie_tmdb_ids = [movie.tmdb_id for _, movie in all_ratings]
        raw_vectors_map = await self.qdrant.get_vectors_batch(movie_tmdb_ids)

        vectors = []
        for _, movie in all_ratings:
            vector = raw_vectors_map.get(movie.tmdb_id)
            if vector:
                vectors.append(vector)
        
        if not vectors:
            return []
        
        global_center = np.mean(vectors, axis=0).tolist()
        
        search_filters = filters or {}
        results = await self.qdrant.search_similar(
            query_vector=global_center,
            limit=limit * 5,
            offset=offset,
            score_threshold=0.15,
            filters=search_filters
        )
        
        if not results:
            results = await self.qdrant.search_similar(
                query_vector=global_center,
                limit=limit * 5,
                offset=offset,
                score_threshold=0.1,
                filters=search_filters
            )
            
        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.is_watched.is_(True))
        )
        watched_movie_ids = set(watched_result.scalars().all())
        
        recommendations = [
            r for r in results
            if r["movie_id"] not in watched_movie_ids
        ][:limit]

        logger.info(f"User {user_id} General Recs: Found {len(results)} raw results. Watched count: {len(watched_movie_ids)}")
        logger.info(f"User {user_id} General Recs: {len(recommendations)} remaining after watched filter.")
        
        return recommendations

    def calculate_quality_weight(self, score: float) -> float:
        """
        Applies a Sigmoid curve to the VectorBox Score (0-100) to get a quality weight (0.0 - 1.0).
        """
        if score is None: 
            return 0.5
            
        k = 0.15
        x0 = 65
        return 1 / (1 + math.exp(-k * (score - x0)))

    @cache(expire=3600)
    async def get_item_based_recommendations(
        self,
        user_id: int,
        db: AsyncSession,
        filters: Dict = None,
        limit: int = 20,
        include_low_quality: bool = False,
        page: int = 1,
        background_tasks = None,
        tmdb: 'TMDBClient' = None
    ) -> List[Dict]:
        """
        Get recommendations using Weighted Item-Item Collaborative Filtering.
        FIX: Batch vector fetch + parallel search_similar to eliminate N+1 sequential calls.
        """
        offset = (page - 1) * limit
        
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.rating >= 3.5, UserRating.is_liked.is_(True)))
            .order_by(desc(UserRating.rating), desc(func.coalesce(UserRating.watched_date, UserRating.created_at)))
        )
        raw_seeds = result.all()
        
        if not raw_seeds:
            return []

        # FIX: Background-only enrichment, never inline.
        from services.movie_service import MovieService
        movie_service = MovieService(db)
        for _, movie in raw_seeds:
            if movie.keywords is None and background_tasks:
                background_tasks.add_task(_enrich_movie_background, movie.tmdb_id)

        # Input De-duplication (Franchise Collapsing)
        collection_map = {}
        standalone_seeds = []
        
        for rating, movie in raw_seeds:
            if movie.collection_id:
                if movie.collection_id not in collection_map:
                    collection_map[movie.collection_id] = []
                collection_map[movie.collection_id].append((rating, movie))
            else:
                standalone_seeds.append((rating, movie))
        
        final_seeds = []
        
        for rating, movie in standalone_seeds:
            final_seeds.append({
                "movie": movie,
                "rating": rating,
                "is_super_seed": False
            })
            
        for cid, items in collection_map.items():
            if len(items) > 1:
                items.sort(key=lambda x: (x[0].rating or 0), reverse=True)
                best_rating, best_movie = items[0]
                final_seeds.append({
                    "movie": best_movie,
                    "rating": best_rating,
                    "is_super_seed": True,
                    "collection_items": items
                })
            else:
                rating, movie = items[0]
                final_seeds.append({
                    "movie": movie,
                    "rating": rating,
                    "is_super_seed": False
                })

        # FIX: Filter qualifying seeds first, then batch-fetch ALL vectors in one call.
        qualifying_seeds = []
        for seed in final_seeds:
            rating_obj = seed["rating"]
            effective_rating = rating_obj.rating
            if effective_rating is None and rating_obj.is_liked:
                effective_rating = 4.5
            if effective_rating is None or effective_rating < 4.0:
                continue
            seed["effective_rating"] = effective_rating
            qualifying_seeds.append(seed)

        if not qualifying_seeds:
            return []

        # ONE batch call to Qdrant instead of N individual get_vector calls
        seed_tmdb_ids = [s["movie"].tmdb_id for s in qualifying_seeds]
        seed_vectors_map = await self.qdrant.get_vectors_batch(seed_tmdb_ids)
        logger.info(f"Item-Based: Fetched {len(seed_vectors_map)}/{len(seed_tmdb_ids)} seed vectors in batch.")

        # FIX: Run all search_similar calls concurrently via asyncio.gather
        async def search_for_seed(seed: Dict):
            movie = seed["movie"]
            vector = seed_vectors_map.get(movie.tmdb_id)
            if not vector:
                return seed, []
            similar = await self.qdrant.search_similar(
                query_vector=vector,
                limit=int(limit * 5),
                offset=offset,
                score_threshold=0.15,
                filters=filters
            )
            return seed, similar

        start_time = time.perf_counter()
        search_results = await asyncio.gather(*[search_for_seed(s) for s in qualifying_seeds])
        duration = time.perf_counter() - start_time
        logger.info(f"Item-Based: {len(qualifying_seeds)} parallel Qdrant searches took {duration:.4f}s")

        # Collect all TMDB IDs across all results for one batch DB lookup
        all_tmdb_ids: set = set()
        for seed, similar in search_results:
            for res in similar:
                if res["movie_id"] != seed["movie"].tmdb_id:
                    all_tmdb_ids.add(res["movie_id"])

        db_movies: Dict[int, Movie] = {}
        if all_tmdb_ids:
            stmt = select(Movie).where(Movie.tmdb_id.in_(all_tmdb_ids))
            movie_res = await db.execute(stmt)
            db_movies = {m.tmdb_id: m for m in movie_res.scalars().all()}

        # Accumulate candidates
        candidates: Dict[int, Dict] = {}

        for seed, similar in search_results:
            movie = seed["movie"]
            effective_rating = seed["effective_rating"]
            
            weight = 1.0 - (5.0 - effective_rating) * 0.05
            if seed["is_super_seed"]:
                weight *= 1.1

            for res in similar:
                tmdb_id = res["movie_id"]
                if tmdb_id == movie.tmdb_id:
                    continue
                
                db_movie = db_movies.get(tmdb_id)
                if not db_movie:
                    continue
                    
                internal_id = db_movie.id
                title = db_movie.title
                vb_score = db_movie.vectorbox_score
                
                effective_score = vb_score if vb_score is not None and vb_score > 0 else 50
                
                if not include_low_quality and effective_score < 40:
                    continue
                
                quality_weight = self.calculate_quality_weight(effective_score)
                similarity = res["score"]
                weighted_score = similarity * quality_weight
                
                if weighted_score > 1.0:
                    weighted_score = 1.0
                
                contribution = weighted_score * weight
                
                if internal_id not in candidates:
                    candidates[internal_id] = {
                        "movie_id": internal_id,
                        "tmdb_id": tmdb_id,
                        "score": 0.0,
                        "contributors": [],
                        "vector": None,
                        "raw_similarity": similarity,
                        "quality_weight": quality_weight,
                        "vb_score": vb_score,
                        "title": title
                    }
                
                candidates[internal_id]["score"] += contribution
                candidates[internal_id]["contributors"].append({
                    "seed_title": movie.title + (" (Franchise)" if seed["is_super_seed"] else ""),
                    "contribution": contribution
                })
        
        # Filter watched/watchlist
        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.is_watched.is_(True))
        )
        watched_ids = set(watched_result.scalars().all())
        
        logger.info(f"Item-Based Recs: Accumulated {len(candidates)} unique candidates from {len(qualifying_seeds)} seeds.")

        watchlist_ids = set()
        watchlist_only = filters.get("watchlist_only", False) if filters else False
        
        if watchlist_only:
            watchlist_result = await db.execute(
                select(UserRating.movie_id)
                .where(UserRating.user_id == user_id)
                .where(UserRating.is_watchlist.is_(True))
                .where(UserRating.is_watched.is_(False))
            )
            watchlist_ids = set(watchlist_result.scalars().all())
        
        pre_mmr_candidates = []
        for c in candidates.values():
            if c["movie_id"] in watched_ids:
                continue
            
            if watchlist_only and c["movie_id"] not in watchlist_ids:
                continue
                
            c["contributors"].sort(key=lambda x: x["contribution"], reverse=True)
            pre_mmr_candidates.append(c)
        
        pre_mmr_candidates.sort(key=lambda x: x["score"], reverse=True)
        
        logger.info(f"Item-Based Recs: {len(pre_mmr_candidates)} candidates remaining after Watched/Watchlist filter.")
        if pre_mmr_candidates:
            top_debug = [f"{c['title']} ({c['score']:.2f}, VB:{c['vb_score']})" for c in pre_mmr_candidates[:5]]
            logger.info(f"Top 5 candidates before Streaming/MMR: {top_debug}")

        # Streaming Filter (Pre-selection)
        valid_candidates = pre_mmr_candidates
        
        if filters and filters.get("streaming_providers"):
            logger.info("Applying Streaming Filters (Pre-MMR)...")
            allowed_provider_ids = set(filters["streaming_providers"])
            country_code = filters.get("country_code", "ES")
            
            pool_size = 300
            candidates_pool = pre_mmr_candidates[:pool_size]
            
            candidate_ids = [c["movie_id"] for c in candidates_pool]
            stmt = select(Movie.id, Movie.tmdb_id).where(Movie.id.in_(candidate_ids))
            tmdb_map_result = await db.execute(stmt)
            id_map = {row.id: row.tmdb_id for row in tmdb_map_result.all()}
            
            from services.provider_service import ProviderService
            
            if not tmdb:
                logger.warning("Streaming filter skipped: no TMDBClient injected.")
            else:
                provider_service = ProviderService(db, tmdb)
                
                providers_map = await provider_service.get_providers_batch(candidate_ids, country_code)
                
                filtered_pool = []
                for cand in candidates_pool:
                     mid = cand["movie_id"]
                     movie_providers = providers_map.get(mid, [])
                     
                     has_provider = False
                     for p in movie_providers:
                         if p["provider_id"] in allowed_provider_ids:
                             has_provider = True
                             break
                     
                     if has_provider:
                         filtered_pool.append(cand)
                
                logger.info(f"Streaming Filter: {len(filtered_pool)} candidates available out of {len(candidates_pool)} checked.")
                valid_candidates = filtered_pool
        
        # MMR Reranking
        pool_size = max(50, limit)
        top_candidates = valid_candidates[:pool_size]
        
        if not top_candidates:
            return []

        candidate_ids = [c["movie_id"] for c in top_candidates]
        final_results = top_candidates
        
        try:
            stmt = select(Movie.id, Movie.tmdb_id).where(Movie.id.in_(candidate_ids))
            res = await db.execute(stmt)
            id_map = {row.id: row.tmdb_id for row in res.all()}
            
            tmdb_ids = [id_map[mid] for mid in candidate_ids if mid in id_map]
            
            if tmdb_ids:
                vectors_map = {}
                points = await self.qdrant.client.retrieve(
                    collection_name=self.qdrant.COLLECTION_NAME,
                    ids=tmdb_ids,
                    with_vectors=True
                )
                for p in points:
                    internal_id = next((iid for iid, tid in id_map.items() if tid == p.id), None)
                    if internal_id:
                        vectors_map[internal_id] = np.array(p.vector)
                
                loop = asyncio.get_running_loop()
                start_time = time.perf_counter()
                
                mmr_func = functools.partial(
                    self.mmr_rerank, 
                    top_candidates, 
                    vectors_map, 
                    limit, 
                    lambda_param=0.7
                )
                
                final_results = await loop.run_in_executor(None, mmr_func)
                
                duration = time.perf_counter() - start_time
                logger.info(f"MMR Reranking took {duration:.4f}s for {len(top_candidates)} candidates")
                
        except Exception as e:
            logger.error(f"MMR Reranking failed: {e}. Returning raw list.")
        
        if final_results:
            max_score = final_results[0]["score"]
            if max_score > 0:
                for res in final_results:
                    res["score"] = 0.50 + (0.20 * (res["score"] / max_score))
        
        return final_results[:limit]

    def mmr_rerank(self, candidates: List[Dict], vectors_map: Dict[int, np.ndarray], limit: int, lambda_param: float = 0.7) -> List[Dict]:
        """
        Maximal Marginal Relevance (MMR) Reranking
        """
        selected = []
        pool = candidates.copy()
        
        while len(selected) < limit and pool:
            best_item = None
            best_mmr_score = -1.0
            
            for item in pool:
                relevance = item["score"]
                max_sim_to_selected = 0.0
                item_vec = vectors_map.get(item["movie_id"])
                
                if item_vec is not None and selected:
                    for selected_item in selected:
                        sel_vec = vectors_map.get(selected_item["movie_id"])
                        if sel_vec is not None:
                            sim = np.dot(item_vec, sel_vec) / (np.linalg.norm(item_vec) * np.linalg.norm(sel_vec))
                            if sim > max_sim_to_selected:
                                max_sim_to_selected = sim
                
                mmr_score = (lambda_param * relevance) - ((1 - lambda_param) * max_sim_to_selected)
                
                if mmr_score > best_mmr_score:
                    best_mmr_score = mmr_score
                    best_item = item
            
            if best_item:
                selected.append(best_item)
                pool.remove(best_item)
            else:
                break
                
        return selected

    async def clear_user_cache(self, user_id: int):
        """
        Manually invalidate the Redis cache for this user's recommendations.
        """
        import os
        import redis.asyncio as redis
        
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        try:
            r = await redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            keys = await r.keys("fastapi-cache:*")
            if keys:
                 await r.delete(*keys)
                 logger.info(f"Cleared {len(keys)} cache keys due to cluster regeneration.")
            await r.close()
        except Exception as e:
            logger.error(f"Failed to clear user cache: {e}")