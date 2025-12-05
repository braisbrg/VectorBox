import logging
import random
from typing import List, Dict, Set, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, or_
from models.database import UserRating, Movie, UserCluster
from models.schemas import FeedSection, FeedItem, FeedResponse
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.qdrant_service import QdrantService
from services.clustering_service import ClusteringService
from services.provider_service import ProviderService

logger = logging.getLogger(__name__)

class FeedService:
    def __init__(self):
        self.clustering = ClusteringService()

    async def create_feed_item(self, movie: Movie, score: float, country: str, tmdb: TMDBClient, include_rating: bool = False, contributors: List[dict] = None, provider_service: ProviderService = None, streaming_providers: List[str] = None) -> FeedItem:
        """Helper to create a FeedItem from a Movie (DB Object)"""
        # Get streaming providers
        if streaming_providers is None:
            streaming_providers = []
            
            if provider_service:
                # Use cached service
                providers_data = await provider_service.get_providers(movie.id, country)
                # ProviderService returns list of dicts with provider_name
                streaming_providers = [p["provider_name"] for p in providers_data]
            else:
                # Fallback to direct TMDB call (legacy)
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
            # Phase 12 Fields
            vectorbox_score=movie.vectorbox_score,
            imdb_rating=movie.imdb_rating,
            metacritic_rating=movie.metacritic_rating,
            rotten_tomatoes_rating=movie.rotten_tomatoes_rating,
            title_es=movie.title_es,
            overview_es=movie.overview_es
        )

    async def create_feed_item_from_tmdb(self, tmdb_data: dict, score: float, country: str, tmdb: TMDBClient) -> FeedItem:
        """Helper to create a FeedItem directly from TMDB data (for non-DB movies)"""
        tmdb_id = tmdb_data.get("id")
        
        # Get streaming providers
        streaming_providers = []
        if tmdb_id:
            providers_data = await tmdb.get_watch_providers(tmdb_id, country)
            if providers_data:
                for provider_type in ["flatrate", "free"]:
                    if provider_type in providers_data:
                        streaming_providers.extend([p["provider_name"] for p in providers_data[provider_type]])
        
        # Scale match score
        # If score is small (0-1), assume it's a raw similarity or weighted score that needs scaling.
        # If score is large (>1), assume it's already a percentage or we should leave it.
        # However, our new logic produces weighted scores in 0-1 range (e.g. 0.4).
        # We want to map these to 0-100.
        
        # New Logic: Just multiply by 100. The caller (ClusteringService) now handles the weighting.
        # If the score is very low (<0.4), it will show as <40% match, which is correct.
        final_score = score * 100
            
        # Extract year
        year = None
        release_date = tmdb_data.get("release_date")
        if release_date:
            try:
                year = int(release_date.split("-")[0])
            except:
                pass

        return FeedItem(
            id=tmdb_id,
            title=tmdb_data.get("title"),
            poster_url=tmdb_data.get("poster_path"),
            match_score=round(final_score, 0),
            streaming_providers=list(set(streaming_providers)),
            year=year,
            runtime=None, # TMDB list response often doesn't have runtime
            letterboxd_uri=f"https://letterboxd.com/tmdb/{tmdb_id}" if tmdb_id else None,
            rating=tmdb_data.get("vote_average"),
            overview=tmdb_data.get("overview"),
            contributors=[],
            # Phase 12 Fields
            vectorbox_score=movie.vectorbox_score if movie else None,
            imdb_rating=None,
            metacritic_rating=None,
            rotten_tomatoes_rating=None,
            title_es=None,
            overview_es=None
        )

    async def get_because_you_watched_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        qdrant: QdrantService,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None
    ) -> FeedSection:
        """Section A: Because you watched [Movie X]"""
        # ... (implementation)
        # Pass provider_service to create_feed_item
        # ...
        # (I need to replace the whole method or just the signature and call?)
        # Let's replace the signature and the create_feed_item calls.
        
        # Find latest movies rated 4+ stars (fetch top 5 candidates)
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(
                UserRating.user_id == user_id,
                UserRating.rating >= 4.0
            )
            .order_by(
                desc(UserRating.created_at)
            )
            .limit(5)
        )
        
        candidates = result.all()
        if not candidates:
        # logger.info(f"No ratings found for user {user_id} in 'Because you watched' section")
            return FeedSection(id="because_you_watched", title="Recommended for You", items=[])
        
        # Try candidates until we find one with recommendations
        for row in candidates:
            user_rating, anchor_movie = row
            
            # Get the vector for the anchor movie
            anchor_vector = await qdrant.get_vector(anchor_movie.id)
            if not anchor_vector:
                continue
            
            # Search for similar movies using the vector
            similar_results = await qdrant.search_similar(
                query_vector=anchor_vector,
                limit=500  # Maximum pool for best variety after deduplication
            )
            
            items = []
            for res in similar_results:
                movie_id = res["movie_id"]
                if movie_id in seen_ids or movie_id == anchor_movie.id:
                    continue
                
                movie_result = await db.execute(select(Movie).where(Movie.id == movie_id))
                movie = movie_result.scalar_one_or_none()
                
                if movie:
                    item = await self.create_feed_item(movie, res["score"], country, tmdb, provider_service=provider_service)
                    items.append(item)
                    seen_ids.add(movie_id)
                    
                    if len(items) >= 10:
                        break
            
            if items:
                return FeedSection(
                    id="because_you_watched",
                    title=f"Because you watched {anchor_movie.title}",
                    items=items
                )
                
        return FeedSection(id="because_you_watched", title="Recommended for You", items=[])

    async def get_your_taste_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None
    ) -> FeedSection:
        """Section B: Your Taste: [Cluster Name]"""
        # Get user's clusters
        clusters_result = await db.execute(
            select(UserCluster)
            .where(UserCluster.user_id == user_id)
            .order_by(desc(UserCluster.movie_count))
        )
        clusters = clusters_result.scalars().all()
        
        if not clusters:
            return FeedSection(id="your_taste", title="Your Taste", items=[])
        
        # Pick dominant or random cluster
        cluster = clusters[0]  # Dominant cluster
        
        # Get recommendations for this cluster
        results = await self.clustering.get_cluster_recommendations(
            user_id=user_id,
            cluster_id=cluster.cluster_id,
            db=db,
            filters={},
            limit=500  # Maximum pool for best variety
        )
        
        items = []
        for res in results:
            movie_id = res["movie_id"]
            if movie_id in seen_ids:
                continue
            
            movie_result = await db.execute(select(Movie).where(Movie.id == movie_id))
            movie = movie_result.scalar_one_or_none()
            
            if movie:
                item = await self.create_feed_item(movie, res["score"], country, tmdb, provider_service=provider_service)
                items.append(item)
                seen_ids.add(movie_id)
                
                if len(items) >= 10:
                    break
        
        title = "Your Taste"
        if cluster.dominant_genres:
            title = ", ".join(cluster.dominant_genres[:3])
            
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
        provider_service: ProviderService = None
    ) -> FeedSection:
        """Section C: Hidden Gems"""
        # Get general recommendations (User-Centric / Clustering)
        results = await self.clustering.get_user_centric_recommendations(
            user_id=user_id,
            db=db,
            filters={"min_vote_count": 50, "min_rating": 5.0},
            limit=500  # Maximum pool for best variety in hidden gems
        )
        
        items = []
        for res in results:
            movie_id = res["movie_id"]
            if movie_id in seen_ids:
                continue
            
            movie_result = await db.execute(select(Movie).where(Movie.id == movie_id))
            movie = movie_result.scalar_one_or_none()
            
            # Filter for hidden gems criteria
            if movie and movie.vote_average and movie.vote_average > 7.0:
                # Note: vote_count might not be in DB, skip if not available
                # Show rating instead of match score for hidden gems
                item = await self.create_feed_item(movie, res["score"], country, tmdb, include_rating=True, provider_service=provider_service)
                items.append(item)
                seen_ids.add(movie_id)
                
                if len(items) >= 10:
                    break
        
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
        """
        Get movies available on user's streaming services.
        PRIORITY: Watchlist items first, then general recommendations.
        """
        items = []
        
        # 1. Fetch User's Watchlist (Unwatched)
        watchlist_result = await db.execute(
            select(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                UserRating.is_watchlist.is_(True),
                UserRating.is_watched.is_(False)
            )
            .limit(200) # Check top 200 watchlist items
        )
        watchlist_movies = watchlist_result.scalars().all()
        
        # 2. Check availability for watchlist items (Optimized with ProviderService)
        if streaming_providers:
            provider_service = ProviderService(db, tmdb)
            
            # Batch fetch providers
            movie_ids = [m.id for m in watchlist_movies if m.tmdb_id not in seen_ids]
            providers_map = await provider_service.get_providers_batch(movie_ids, country)
            
            for movie in watchlist_movies:
                if movie.tmdb_id in seen_ids:
                    continue
                    
                # Check availability
                available_providers = []
                movie_providers = providers_map.get(movie.id, [])
                
                for p in movie_providers:
                    if p["provider_id"] in streaming_providers:
                        available_providers.append(p["provider_name"])
                
                if available_providers:
                    # It's available! Add to feed.
                    # Calculate dynamic match score based on rating or default high
                    match_score = 95
                    if movie.vote_average:
                        # Map 7.0-9.0 to 90-99%
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
                        # Phase 12 Fields
                        vectorbox_score=movie.vectorbox_score,
                        imdb_rating=movie.imdb_rating,
                        metacritic_rating=movie.metacritic_rating,
                        rotten_tomatoes_rating=movie.rotten_tomatoes_rating,
                        title_es=movie.title_es,
                        overview_es=movie.overview_es,
                        overview=movie.overview
                    ))
                    seen_ids.add(movie.tmdb_id)
                    
                    if len(items) >= 20: # Limit watchlist items to 20
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
        include_low_quality: bool = False
    ) -> FeedSection:
        """
        Section: Deep Dive (Item-Based Collaborative Filtering)
        """
        results = await self.clustering.get_item_based_recommendations(
            user_id=user_id,
            db=db,
            limit=20,
            include_low_quality=include_low_quality
        )
        
        items = []
        for res in results:
            movie_id = res["movie_id"]
            if movie_id in seen_ids:
                continue
                
            movie_result = await db.execute(select(Movie).where(Movie.id == movie_id))
            movie = movie_result.scalar_one_or_none()
            
            if movie:
                # Extract contributors from result
                contributors = res.get("contributors", [])
                item = await self.create_feed_item(movie, res["score"], country, tmdb, contributors=contributors, provider_service=provider_service)
                items.append(item)
                seen_ids.add(movie_id)
                
                if len(items) >= 10:
                    break
                    
        return FeedSection(
            id="deep_dive",
            title="Deep Dive",
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
        """
        Wildcard Section: "Outside Your Comfort Zone"
        Logic: Recommend high-rated movies that do NOT match the user's dominant genres.
        """
        try:
            # 1. Get user's dominant genres from clusters
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
                # If no clusters/genres, fallback to random high rated
                return await self.get_random_recommendations_section(user_id, db, tmdb, seen_ids, country, provider_service)

            # 2. Get high rated movies from DB
            # We fetch a larger pool to filter
            result = await db.execute(
                select(Movie)
                .where(Movie.vote_average > 7.0)
                .where(Movie.vote_count > 100)
                .order_by(desc(Movie.vote_average))
                .limit(1000)
            )
            candidates = result.scalars().all()
            
            # 3. Filter out movies that match excluded genres AND exclude watched movies
            
            # Get watched movies
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
                # If the movie has NO overlap with excluded genres, it's a candidate
                if movie_genres.isdisjoint(excluded_genres):
                    wildcard_candidates.append(m)
            
            if not wildcard_candidates:
                return None
                
            # 4. Pick random 10
            import random
            sample = random.sample(wildcard_candidates, min(10, len(wildcard_candidates)))
            
            items = []
            for m in sample:
                item = await self.create_feed_item(m, 0.85, country, tmdb, include_rating=True, provider_service=provider_service)
                items.append(item)
                seen_ids.add(m.tmdb_id)
                
            return FeedSection(
                id="wildcard",
                title="Outside Your Comfort Zone",
                items=items
            )
            
        except Exception as e:
            logger.error(f"Wildcard generation failed: {e}")
            return None

    async def get_random_recommendations_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        seen_ids: Set[int],
        country: str,
        provider_service: ProviderService = None
    ) -> Optional[FeedSection]:
        """
        Random Picks: Random selection from Top 500 VectorBox Scored movies in DB.
        """
        try:
            # 1. Get Top 500 movies by VectorBox Score
            # If VB score is null, fallback to vote_average? No, user requested VB score.
            result = await db.execute(
                select(Movie)
                .where(Movie.vectorbox_score.isnot(None))
                .order_by(desc(Movie.vectorbox_score))
                .limit(500)
            )
            candidates = result.scalars().all()
            
            if not candidates:
                # Fallback to vote_average if no VB scores yet
                result = await db.execute(
                    select(Movie)
                    .where(Movie.vote_average > 7.0)
                    .order_by(desc(Movie.vote_average))
                    .limit(500)
                )
                candidates = result.scalars().all()
            
            # Filter seen
            # Also filter watched history?
            watched_result = await db.execute(
                select(UserRating.movie_id)
                .where(UserRating.user_id == user_id, UserRating.is_watched == True)
            )
            watched_ids = set(watched_result.scalars().all())
            
            unseen_candidates = [m for m in candidates if m.id not in seen_ids and m.id not in watched_ids]
            
            if not unseen_candidates:
                return None
                
            # Pick 10 random
            import random
            sample = random.sample(unseen_candidates, min(10, len(unseen_candidates)))
            
            items = []
            for m in sample:
                # For Random row, show VB Score if available, or just high match?
                # User said: "in this row show only the VB score"
                # create_feed_item handles score display logic usually, but we can pass a flag or handle in UI.
                # We'll pass a dummy match score (0.9) but ensure VB score is populated.
                item = await self.create_feed_item(m, 0.9, country, tmdb, include_rating=True, provider_service=provider_service)
                items.append(item)
                seen_ids.add(m.tmdb_id)
                
            return FeedSection(
                id="random_picks", 
                title="Random Top Picks", 
                items=items
            )

        except Exception as e:
            logger.error(f"Random generation failed: {e}")
            return None

    async def get_random_watchlist_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        country: str
    ) -> Optional[FeedSection]:
        """
        Get random movies from the user's watchlist.
        """
        # Get all watchlist items
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
            
        # Pick 20 random
        selected_movies = random.sample(candidates, min(20, len(candidates)))
        
        items = []
        for movie in selected_movies:
            item = await self.create_feed_item(movie, 1.0, country, tmdb, include_rating=True)
            items.append(item)
            
        return FeedSection(
            id="random_watchlist",
            title="Shuffle: From Your Watchlist",
            items=items
        )

    async def get_watchlist_feed(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        country: str,
        streaming_providers: List[int]
    ) -> FeedResponse:
        """
        Generate a feed based ONLY on the user's watchlist.
        """
        # 1. Available Now (Watchlist)
        available_section = await self.get_available_now_section(user_id, db, tmdb, set(), country, streaming_providers)
        
        # 2. Top Rated in Watchlist
        top_rated_result = await db.execute(
            select(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                UserRating.is_watchlist.is_(True),
                UserRating.is_watched.is_(False)
            )
            .order_by(desc(Movie.vectorbox_score))
            .limit(20)
        )
        top_rated_movies = top_rated_result.scalars().all()
        top_rated_items = []
        for movie in top_rated_movies:
            item = await self.create_feed_item(movie, 1.0, country, tmdb, include_rating=True)
            top_rated_items.append(item)
            
        top_rated_section = FeedSection(
            id="watchlist_top_rated",
            title="Top Rated in Your Watchlist",
            items=top_rated_items
        )
        
        # 3. Short & Sweet (Runtime < 100m)
        short_result = await db.execute(
            select(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                UserRating.is_watchlist.is_(True),
                UserRating.is_watched.is_(False),
                Movie.runtime < 100
            )
            .order_by(desc(Movie.vote_average))
            .limit(20)
        )
        short_movies = short_result.scalars().all()
        short_items = []
        for movie in short_movies:
            item = await self.create_feed_item(movie, 1.0, country, tmdb, include_rating=True)
            short_items.append(item)
            
        short_section = FeedSection(
            id="watchlist_short",
            title="Short & Sweet (Watchlist)",
            items=short_items
        )

        # 4. Random Shuffle
        random_section = await self.get_random_watchlist_section(user_id, db, tmdb, country)
        
        sections = [available_section, top_rated_section, short_section, random_section]
        feed = [s for s in sections if s and s.items]
        
        return FeedResponse(feed=feed)

    async def get_popular_on_letterboxd_section(
        self,
        user_id: int,
        db: AsyncSession,
        tmdb: TMDBClient,
        country: str,
        provider_service: ProviderService = None
    ) -> Optional[FeedSection]:
        """
        Section: Popular on Letterboxd This Week
        """
        from services.trending_service import TrendingService
        
        trending_service = TrendingService(db)
        popular_ids = trending_service.get_popular_movie_ids()
        
        if not popular_ids:
            return None
            
        # Fetch movies from DB
        result = await db.execute(
            select(Movie).where(Movie.tmdb_id.in_(popular_ids))
        )
        movies_map = {m.tmdb_id: m for m in result.scalars().all()}
        
        # Maintain order from Redis
        items = []
        for tmdb_id in popular_ids:
            movie = movies_map.get(tmdb_id)
            if movie:
                # Create item (High match score for trending items)
                item = await self.create_feed_item(
                    movie, 
                    0.95, # High score for trending
                    country, 
                    tmdb, 
                    include_rating=True, 
                    provider_service=provider_service
                )
                
                # Override rating with Letterboxd specific if available
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
