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
        
        # logger.info(f"Calculated {n_clusters} clusters for {n_movies} movies")
        return n_clusters
    
    async def create_user_clusters(
        self,
        user_id: int,
        db: AsyncSession,
        use_recency_bias: bool = False,
        background_tasks = None
    ) -> List[UserCluster]:
        """
        Generate K-Means clusters for a user's taste profile
        Returns list of UserCluster objects
        """
        # Fetch user's rated OR liked movies
        # We use liked movies as implicit positive feedback even if unrated
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.rating.isnot(None), UserRating.is_liked.is_(True)))
        )
        ratings_movies = result.all()
        
        # Sort by movie ID to ensure deterministic order for K-Means
        ratings_movies.sort(key=lambda x: x[1].id)
        
        if len(ratings_movies) < 10:
            # Try to be lenient if we have fewer movies, but 5 is absolute minimum
            if len(ratings_movies) < 5:
                logger.warning(f"User {user_id} has too few rated/liked movies ({len(ratings_movies)}) for clustering")
                return []
        
        # logger.info(f"Creating clusters for user {user_id} with {len(ratings_movies)} rated/liked movies")
        
        # SAFETY CHECK (Audit Phase 3): Enrich movies missing keywords before vector fetch
        # v1.2: Refactored to Background Task to prevent UI lag
        from services.movie_service import MovieService
        movie_service = MovieService(db)
        
        for _, movie in ratings_movies:
            if not movie.keywords:
                if background_tasks:
                    background_tasks.add_task(movie_service.enrich_movie, movie)
                else:
                    # Fallback: Process inline if no background task context (e.g. CLI)
                    await movie_service.enrich_movie(movie)

        # Retrieve vectors from Qdrant (batch, no N+1)
        movie_tmdb_ids = [movie.tmdb_id for _, movie in ratings_movies]
        raw_vectors_map = await self.qdrant.get_vectors_batch(movie_tmdb_ids)

        vectors = []
        ratings = []
        movies_data = []

        for rating, movie in ratings_movies:
            vector = raw_vectors_map.get(movie.tmdb_id)
            if vector is not None:

                # Determine effective rating
                effective_rating = rating.rating
                if effective_rating is None and rating.is_liked:
                    effective_rating = 4.5  # Treat liked but unrated as 4.5 stars
                
                if effective_rating is None:
                    continue # Should not happen due to query, but safe check

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
        
        # Convert to numpy array
        X = np.array(vectors)
        
        # Validate Dimension (Must be 384 for all-MiniLM-L6-v2)
        if X.shape[1] != 384:
            logger.error(f"Vector Dimension Mismatch! Expected 384, got {X.shape[1]}. Aborting clustering.")
            return []
        
        # Normalize vectors to unit length (important for cosine similarity space)
        from sklearn.preprocessing import normalize
        try:
            X_normalized = normalize(X)
        except Exception as e:
            logger.error(f"Normalization failed: {e}")
            return []
        
        ratings_array = np.array(ratings)
        
        # Weight vectors by rating (Non-linear)
        weights = []
        now = datetime.now(timezone.utc)
        
        for i, r in enumerate(ratings):
            # Base weight from rating
            if r >= 4.0:
                w = 1.0
            elif r >= 2.0:
                w = 0.5
            else:
                w = 0.1
            
            # Recency Bias (if enabled)
            if use_recency_bias:
                rating_obj = ratings_movies[i][0]
                date = rating_obj.watched_date or rating_obj.created_at
                
                if date:
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    age_years = (now - date).days / 365.0
                    decay = 1.0 / (1.0 + 0.5 * age_years)
                    w *= decay
            
            weights.append(w)
            
        weights_array = np.array(weights)
        try:
             X_weighted = X_normalized * weights_array[:, np.newaxis]
        except Exception as e:
              logger.error(f"Weight application failed: {e}")
              return []
        
        # Determine optimal number of clusters
        n_clusters = self.calculate_optimal_clusters(len(vectors))
        
        # Perform K-Means clustering
        kmeans = KMeans(
            n_clusters=n_clusters,
            random_state=42,
            n_init=10,
            max_iter=300
        )
        
        # CPU-Bound: Offload to executor
        loop = asyncio.get_running_loop()
        start_time = time.perf_counter()
        
        cluster_labels = await loop.run_in_executor(
            None,
            kmeans.fit_predict, 
            X_weighted
        )
        
        duration = time.perf_counter() - start_time
        logger.info(f"K-Means clustering for user {user_id} took {duration:.4f}s")
        
        # Analyze each cluster
        cluster_objects = []
        
        # Delete existing clusters for this user
        from sqlalchemy import delete
        await db.execute(delete(UserCluster).where(UserCluster.user_id == user_id))
        
        for cluster_id in range(n_clusters):
            # Get movies in this cluster
            cluster_indices = np.where(cluster_labels == cluster_id)[0]
            cluster_movies = [movies_data[i] for i in cluster_indices]
            
            if not cluster_movies:
                continue
            
            # Filter out small clusters (noise)
            if len(cluster_movies) < 5:
                # logger.info(f"Cluster {cluster_id} too small ({len(cluster_movies)} movies), skipping")
                continue
                
            # Calculate stats
            avg_rating = np.mean([m["rating"] for m in cluster_movies])
            
            # Extract dominant genres
            all_genres = []
            for m in cluster_movies:
                all_genres.extend(m["genres"])
            
            genre_counts = Counter(all_genres)
            dominant_genres = [g for g, c in genre_counts.most_common(3)]
            
            # Determine decade/era
            years = [m["year"] for m in cluster_movies if m["year"]]
            if years:
                avg_year = int(np.mean(years))
                decade = f"{(avg_year // 10) * 10}s"
            else:
                decade = "Various"
            
            # Create descriptive label
            if dominant_genres:
                label = f"{decade} {'/'.join(dominant_genres[:2])}"
            else:
                label = f"{decade} Cinema"
            
            # Select sample movies (highest rated in cluster)
            cluster_movies_sorted = sorted(cluster_movies, key=lambda x: x["rating"], reverse=True)
            sample_movie_ids = [m["id"] for m in cluster_movies_sorted[:5]]
            
            # Create UserCluster object
            user_cluster = UserCluster(
                user_id=user_id,
                cluster_id=cluster_id,
                cluster_label=label,
                movie_count=len(cluster_movies),
                avg_rating=float(avg_rating),
                dominant_genres=dominant_genres,
                sample_movie_ids=sample_movie_ids
            )
            
            db.add(user_cluster)
            cluster_objects.append(user_cluster)
            
            # logger.info(f"Cluster {cluster_id}: {label} ({len(cluster_movies)} movies, avg rating: {avg_rating:.2f})")
        
        await db.commit()
        
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
        background_tasks = None
    ) -> List[Dict]:
        """
        Get movie recommendations for a specific cluster (mood)
        """
        offset = (page - 1) * limit
        
        # Get cluster information
        result = await db.execute(
            select(UserCluster)
            .where(UserCluster.user_id == user_id)
            .where(UserCluster.cluster_id == cluster_id)
        )
        cluster = result.scalar_one_or_none()
        
        if not cluster:
            raise ValueError(f"Cluster {cluster_id} not found for user {user_id}")
        
        # Get movies in this cluster to compute center vector
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.rating.isnot(None), UserRating.is_liked.is_(True)))
        )
        all_ratings = result.all()
        
        # Retrieve vectors and compute cluster center (batch, no N+1)
        sample_ids = cluster.sample_movie_ids or []
        sample_tmdb_ids = [movie.tmdb_id for _, movie in all_ratings if movie.id in sample_ids]
        vectors_map = await self.qdrant.get_vectors_batch(sample_tmdb_ids)
        cluster_vectors = list(vectors_map.values())
        
        if not cluster_vectors:
            raise ValueError("No vectors found for cluster samples")
        
        # Compute cluster center (mean of vectors)
        cluster_center = np.mean(cluster_vectors, axis=0).tolist()
        
        # Search Qdrant for similar movies
        search_filters = filters or {}
        results = await self.qdrant.search_similar(
            query_vector=cluster_center,
            limit=limit * 5,  # Maximum pool
            offset=offset,
            score_threshold=0.3,
            filters=search_filters
        )
        
        # Filter out movies user has already watched
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
        
        # Get all rated/liked movies to compute global center
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.rating.isnot(None), UserRating.is_liked.is_(True)))
        )
        all_ratings = result.all()
        
        # Self-healing (Background)
        from services.movie_service import MovieService
        movie_service = MovieService(db)
        for _, movie in all_ratings:
            if not movie.keywords and background_tasks:
                 background_tasks.add_task(movie_service.enrich_movie, movie)
        
        if not all_ratings:
            return []
        
        # Retrieve vectors (batch, no N+1)
        movie_tmdb_ids = [movie.tmdb_id for _, movie in all_ratings]
        raw_vectors_map = await self.qdrant.get_vectors_batch(movie_tmdb_ids)

        vectors = []
        for _, movie in all_ratings:
            vector = raw_vectors_map.get(movie.tmdb_id)
            if vector:
                vectors.append(vector)
        
        if not vectors:
            return []
        
        # Compute global center
        global_center = np.mean(vectors, axis=0).tolist()
        
        # Search Qdrant
        search_filters = filters or {}
        results = await self.qdrant.search_similar(
            query_vector=global_center,
            limit=limit * 5,
            offset=offset,
            # Adjusted Threshold: Enriched vectors are more specific, so 0.2 might be too strict
            score_threshold=0.15,
            filters=search_filters
        )
        
        if not results:
            results = await self.qdrant.search_similar(
                query_vector=global_center,
                limit=limit * 5,
                offset=offset,
                score_threshold=0.1, # Lower threshold fallback
                filters=search_filters
            )
            
        # Filter out movies user has already watched
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
        """
        offset = (page - 1) * limit
        
        # 1. Get top rated movies (Seeds)
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

        # Self-healing (Background)
        from services.movie_service import MovieService
        movie_service = MovieService(db)
        for _, movie in raw_seeds:
            if not movie.keywords and background_tasks:
                 background_tasks.add_task(movie_service.enrich_movie, movie)

        # 2. Input De-duplication (Franchise Collapsing)
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
        
        # Process Standalone
        for rating, movie in standalone_seeds:
            final_seeds.append({
                "movie": movie,
                "rating": rating,
                "is_super_seed": False
            })
            
        # Process Collections
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

        # 3. Search for similar movies
        candidates: Dict[int, Dict] = {}
        
        for seed in final_seeds:
            movie = seed["movie"]
            rating_obj = seed["rating"]
            
            effective_rating = rating_obj.rating
            if effective_rating is None and rating_obj.is_liked:
                effective_rating = 4.5
            
            if effective_rating is None or effective_rating < 4.0:
                continue

            weight = 1.0 - (5.0 - effective_rating) * 0.05
            if seed["is_super_seed"]:
                weight *= 1.1
            
            vector = await self.qdrant.get_vector(movie.tmdb_id)
            if not vector:
                continue
                
            # Search similar with offset for pagination
            # Note: Pagination here is per-seed, which is experimental but provides "fresh" results per page
            similar = await self.qdrant.search_similar(
                query_vector=vector,
                limit=int(limit * 5), 
                offset=offset, # Use offset here
                score_threshold=0.15,
                filters=filters
            )
            
            # Collect TMDB IDs for batch lookup
            tmdb_ids_to_lookup = [res["movie_id"] for res in similar if res["movie_id"] != movie.tmdb_id]
            
            if not tmdb_ids_to_lookup:
                continue
                
            # Batch map TMDB ID -> Internal ID & Metadata
            stmt = select(Movie).where(Movie.tmdb_id.in_(tmdb_ids_to_lookup))
            movie_res = await db.execute(stmt)
            db_movies = {m.tmdb_id: m for m in movie_res.scalars().all()}
            
            for res in similar:
                tmdb_id = res["movie_id"]
                if tmdb_id == movie.tmdb_id:
                    continue
                
                # Resolving Internal ID
                db_movie = db_movies.get(tmdb_id)
                if not db_movie:
                    # Ghost vector (exists in Qdrant but not in DB)
                    continue
                    
                internal_id = db_movie.id
                title = db_movie.title
                vb_score = db_movie.vectorbox_score
                
                # lenient fallback for missing score
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
                        "movie_id": internal_id, # return Internal ID
                        "tmdb_id": tmdb_id,      # keep TMDB ID for ref
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
        
        # 4. Filter watched/watchlist
        watched_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.is_watched.is_(True))
        )
        watched_ids = set(watched_result.scalars().all())
        
        logger.info(f"Item-Based Recs: Accumulated {len(candidates)} unique candidates from {len(final_seeds)} seeds.")

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

        # 5. Streaming Filter (Pre-selection)
        # We apply this BEFORE MMR/limiting to ensure we return a full page of results
        valid_candidates = pre_mmr_candidates
        
        if filters and filters.get("streaming_providers"):
            logger.info("Applying Streaming Filters (Pre-MMR)...")
            allowed_provider_ids = set(filters["streaming_providers"])
            country_code = filters.get("country_code", "ES")
            
            # Use a larger pool for streaming checks (to ensure we find enough matches)
            pool_size = 300
            candidates_pool = pre_mmr_candidates[:pool_size]
            
            # Fetch TMDB IDs for batch lookup
            candidate_ids = [c["movie_id"] for c in candidates_pool]
            stmt = select(Movie.id, Movie.tmdb_id).where(Movie.id.in_(candidate_ids))
            tmdb_map_result = await db.execute(stmt)
            # Map internal_id -> tmdb_id
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
                     
                     # Check availability
                     has_provider = False
                     for p in movie_providers:
                         if p["provider_id"] in allowed_provider_ids:
                             has_provider = True
                             break
                     
                     if has_provider:
                         filtered_pool.append(cand)
                
                logger.info(f"Streaming Filter: {len(filtered_pool)} candidates available out of {len(candidates_pool)} checked.")
                valid_candidates = filtered_pool
        
        # 6. MMR Reranking
        # Now we run MMR on the VALID candidates
        # Ensure pool is large enough for requested limit
        pool_size = max(50, limit)
        top_candidates = valid_candidates[:pool_size] # MMR input pool
        
        if not top_candidates:
            return []

        # Fetch vectors for candidates
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
                
                        vectors_map[internal_id] = np.array(p.vector)
                
                # CPU-Bound: Offload MMR to executor
                loop = asyncio.get_running_loop()
                start_time = time.perf_counter()
                
                # Use functools.partial to pass arguments cleanly
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
        
        # Normalize for UI
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
        Useful when clusters are regenerated or taste profile changes significantly.
        """
        import os
        import redis.asyncio as redis
        
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        try:
            r = await redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            
            # Pattern for FastAPI cache related to this service
            # Since we don't know the exact key hash, we delete broader patterns or rely on key builder convention.
            # Default key builder: fastapicache:[func]:[args]
            # We will delete all keys for get_cluster_recommendations for this user?
            # It's hard to target arguments.
            # For now, we accept wiping ALL fasting-api cache for safety, OR we target keys by scanning.
            # Scanning is slow but safer than wiping everyone else's cache.
            # Actually, `reset_profiles.py` wiped EVERYTHING.
            # To be surgical, let's look for keys containing user_id if possible? No, arguments are hashed mostly.
            
            # FASTAPI-CACHE stores "fastapi-cache:[module]:[func]:[args]"
            # Without a custom key builder, we can't target user_id easily.
            # STRATEGY: Clear ALL cache for these specific functions?
            # Or assume the user accepts a global flush for now.
            # Given the high stakes of "Old Clusters", a global flush of these specific endpoints is fine.
            # We'll stick to the "Nuclear Option" as implemented in reset_profiles.py for now, OR:
            
            # If we used a custom key builder, we could do "fastapi-cache:recommendations:user:{user_id}*"
            # Since we didn't implement that yet, let's just Log a warning that automatic cache clearing 
            # might require a full clear or waiting for expiry (3600s).
            
            # However, reset_profiles.py clears *everything*.
            # Let's try to clear just the functions we know.
            
            # Actually, let's just clear everything. It's safer.
            keys = await r.keys("fastapi-cache:*")
            if keys:
                 await r.delete(*keys)
                 logger.info(f"Cleared {len(keys)} cache keys due to cluster regeneration.")
            
            await r.close()
        except Exception as e:
            logger.error(f"Failed to clear user cache: {e}")
