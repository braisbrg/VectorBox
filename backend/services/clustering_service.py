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
from sqlalchemy import select, or_, desc
from fastapi_cache.decorator import cache

from models.database import UserRating, Movie, UserCluster
from services.qdrant_service import QdrantService

logger = logging.getLogger(__name__)


class ClusteringService:
    """Create and manage user taste clusters"""
    
    def __init__(self):
        self.qdrant = QdrantService()
    
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
        use_recency_bias: bool = False
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
        
        # Retrieve vectors from Qdrant
        vectors = []
        ratings = []
        movies_data = []
        
        for rating, movie in ratings_movies:
            vector = await self.qdrant.get_vector(movie.id)
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
            logger.warning("Insufficient movie vectors for clustering")
            return []
        
        # Convert to numpy array
        X = np.array(vectors)
        
        # Normalize vectors to unit length (important for cosine similarity space)
        from sklearn.preprocessing import normalize
        X_normalized = normalize(X)
        
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
        X_weighted = X_normalized * weights_array[:, np.newaxis]
        
        # Determine optimal number of clusters
        n_clusters = self.calculate_optimal_clusters(len(vectors))
        
        # Perform K-Means clustering
        kmeans = KMeans(
            n_clusters=n_clusters,
            random_state=42,
            n_init=10,
            max_iter=300
        )
        cluster_labels = kmeans.fit_predict(X_weighted)
        
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
        page: int = 1
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
        
        # Retrieve vectors and compute cluster center
        cluster_vectors = []
        for rating, movie in all_ratings:
            if movie.id in (cluster.sample_movie_ids or []):
                vector = await self.qdrant.get_vector(movie.id)
                if vector:
                    cluster_vectors.append(vector)
        
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
        page: int = 1
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
        
        if not all_ratings:
            return []
        
        # Retrieve vectors
        vectors = []
        for rating, movie in all_ratings:
            vector = await self.qdrant.get_vector(movie.id)
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
            score_threshold=0.2,
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
        page: int = 1
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
            .order_by(desc(UserRating.rating), desc(UserRating.created_at))
        )
        raw_seeds = result.all()
        
        if not raw_seeds:
            return []

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
            
            vector = await self.qdrant.get_vector(movie.id)
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
            
            for res in similar:
                movie_id = res["movie_id"]
                if movie_id == movie.id:
                    continue
                
                payload = res.get("payload", {})
                vb_score = payload.get("vectorbox_score")
                title = payload.get("title", "Unknown")
                
                if vb_score is None or vb_score == 0 or title == "Unknown":
                    stmt = select(Movie).where(Movie.id == movie_id)
                    movie_res = await db.execute(stmt)
                    db_movie = movie_res.scalar_one_or_none()
                    if db_movie:
                        vb_score = db_movie.vectorbox_score
                        title = db_movie.title
                
                if not title or title == "Unknown" or not vb_score:
                    continue

                if not include_low_quality and vb_score < 40:
                    continue
                
                quality_weight = self.calculate_quality_weight(vb_score)
                similarity = res["score"]
                weighted_score = similarity * quality_weight
                
                if weighted_score > 1.0:
                    weighted_score = 1.0
                
                contribution = weighted_score * weight
                
                if movie_id not in candidates:
                    candidates[movie_id] = {
                        "movie_id": movie_id,
                        "score": 0.0,
                        "contributors": [],
                        "vector": None,
                        "raw_similarity": similarity,
                        "quality_weight": quality_weight,
                        "vb_score": vb_score,
                        "title": title
                    }
                
                candidates[movie_id]["score"] += contribution
                candidates[movie_id]["contributors"].append({
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
        
        # 5. MMR Reranking
        top_candidates = pre_mmr_candidates[:50]
        
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
                points = self.qdrant.client.retrieve(
                    collection_name=self.qdrant.COLLECTION_NAME,
                    ids=tmdb_ids,
                    with_vectors=True
                )
                for p in points:
                    internal_id = next((iid for iid, tid in id_map.items() if tid == p.id), None)
                    if internal_id:
                        vectors_map[internal_id] = np.array(p.vector)
                
                final_results = self.mmr_rerank(top_candidates, vectors_map, limit, lambda_param=0.7)
                
        except Exception as e:
            logger.error(f"MMR Reranking failed: {e}. Returning raw list.")
        
        # Post-filter: Streaming Providers
        if filters and filters.get("streaming_providers"):
            allowed_provider_ids = set(filters["streaming_providers"])
            filtered_recs = []
            
            candidates_to_check = final_results[:50]
            candidate_ids = [rec["movie_id"] for rec in candidates_to_check]
            tmdb_map_result = await db.execute(
                select(Movie.id, Movie.tmdb_id)
                .where(Movie.id.in_(candidate_ids))
            )
            tmdb_map = {mid: tid for mid, tid in tmdb_map_result.all()}
            
            from services.tmdb_client import TMDBClient
            tmdb = TMDBClient()
            
            for rec in candidates_to_check:
                tmdb_id = tmdb_map.get(rec["movie_id"])
                if not tmdb_id:
                    continue
                    
                providers = await tmdb.get_movie_watch_providers(tmdb_id, country_code=filters.get("country_code", "ES"))
                if providers and providers.get("flatrate"):
                    movie_provider_ids = set(p["provider_id"] for p in providers["flatrate"])
                    if not allowed_provider_ids.isdisjoint(movie_provider_ids):
                        filtered_recs.append(rec)
                
            await tmdb.close()
            final_results = filtered_recs
        
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
