import logging
import random
from typing import List, Dict, Set, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

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

class RecommendationEngine:
    """
    Core engine for generating recommendation strategies.
    Decoupled from the FeedService orchestration layer.
    """

    def __init__(self, qdrant: QdrantService = None, embedding_service: EmbeddingService = None):
        self.clustering = ClusteringService(qdrant=qdrant)
        self.embedding_service = embedding_service
        if self.embedding_service is None:
            logger.warning("EmbeddingService not injected into RecommendationEngine.")

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
        with _tracer.start_as_current_span("trident.signal_a.because_you_watched") as span:
            span.set_attribute("user_id", user_id)
            span.set_attribute("country", country)

            result = await db.execute(
                select(UserRating, Movie)
                .join(Movie, UserRating.movie_id == Movie.id)
                .where(
                    UserRating.user_id == user_id,
                    UserRating.rating >= 4.0
                )
                .order_by(
                    desc(func.coalesce(UserRating.watched_date, UserRating.created_at))
                )
                .limit(5)
            )
            
            candidates = result.all()
            if not candidates:
                span.set_attribute("result_count", 0)
                return FeedSection(id="because_you_watched", title="Recommended for You", items=[])
            
            for row in candidates:
                user_rating, anchor_movie = row
                
                if not self.embedding_service:
                    # No embedding service — fall back to stored vector
                    anchor_vector = await qdrant.get_vector(anchor_movie.tmdb_id)
                else:
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
                
                similar_results = await qdrant.search_similar(
                    query_vector=anchor_vector,
                    limit=500,
                    score_threshold=0.1
                )
                
                items = []
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

                for res in similar_results:
                    movie_id = res["movie_id"]
                    if movie_id in seen_ids or movie_id == anchor_movie.tmdb_id:
                        continue
                    
                    movie = movie_map.get(movie_id)
                    if movie:
                        p_data = providers_map.get(movie.id, [])
                        s_providers = [p["provider_name"] for p in p_data]
                        
                        item = await self.create_feed_item(
                            movie, res["score"], country, tmdb, 
                            provider_service=provider_service,
                            streaming_providers=s_providers
                        )
                        items.append(item)
                        seen_ids.add(movie_id)
                        
                        if len(items) >= 10:
                            break
                
                if items:
                    span.set_attribute("result_count", len(items))
                    span.set_attribute("anchor_movie", anchor_movie.title)
                    return FeedSection(
                        id="because_you_watched",
                        title=f"Because you watched {anchor_movie.title}",
                        items=items
                    )
                    
            span.set_attribute("result_count", 0)
            return FeedSection(id="because_you_watched", title="Recommended for You", items=[])

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
                return FeedSection(id="your_taste", title="Your Taste", items=[])
            
            cluster = clusters[0]
            results = await self.clustering.get_cluster_recommendations(
                user_id=user_id,
                cluster_id=cluster.cluster_id,
                db=db,
                filters={},
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
                
            items = []
            for res in results:
                movie_id = res["movie_id"]
                if movie_id in seen_ids:
                    continue
                
                movie = movie_map.get(movie_id)
                if movie:
                    p_data = providers_map.get(movie.id, [])
                    s_providers = [p["provider_name"] for p in p_data]
                    
                    item = await self.create_feed_item(
                        movie, res["score"], country, tmdb, 
                        provider_service=provider_service,
                        streaming_providers=s_providers
                    )
                    items.append(item)
                    seen_ids.add(movie_id)
                    
                    if len(items) >= 10:
                        break
            
            title = "Your Taste"
            if cluster.dominant_genres:
                title = ", ".join(cluster.dominant_genres[:3])

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

            results = await self.clustering.get_user_centric_recommendations(
                user_id=user_id,
                db=db,
                filters={
                    "min_vectorbox_score": 75,
                    "max_popularity": 20,
                    "min_vote_count": 500
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
                 
            items = []
            for res in results:
                movie_id = res["movie_id"]
                if movie_id in seen_ids:
                    continue
                
                movie = movie_map.get(movie_id)
                if movie and movie.vectorbox_score and movie.vectorbox_score > 75:
                    p_data = providers_map.get(movie.id, [])
                    s_providers = [p["provider_name"] for p in p_data]
                    
                    item = await self.create_feed_item(
                        movie, res["score"], country, tmdb, 
                        include_rating=True, provider_service=provider_service,
                        streaming_providers=s_providers
                    )
                    items.append(item)
                    seen_ids.add(movie_id)
                    
                    if len(items) >= 10:
                        break
            
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
            .where(UserRating.user_id == user_id, UserRating.is_watched == True)
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
            .where(UserRating.user_id == user_id, UserRating.is_watched == True)
        )
        watched_ids = set(watched_result.scalars().all())
        
        unseen_candidates = [m for m in candidates if m.id not in seen_ids and m.id not in watched_ids]
        
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
        popular_ids = trending_service.get_popular_movie_ids()
        
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
