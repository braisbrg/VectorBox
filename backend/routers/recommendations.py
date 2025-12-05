from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, or_
from typing import List, Optional, Set, Dict
import random
import logging
import asyncio

from database import get_db
from models.database import UserRating, Movie, UserCluster
from models.schemas import (
    RecommendationRequest, 
    RecommendationResponse, 
    MovieMetadata, 
    ClusterInfo,
    GroupRecommendationRequest,
    FeedResponse,
    FeedSection,
    FeedItem
)
from services.clustering_service import ClusteringService
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.feed_service import FeedService
from services.provider_service import ProviderService

router = APIRouter(
    tags=["recommendations"]
)

logger = logging.getLogger(__name__)

async def _enrich_recommendations(
    results: List[Dict],
    user_id: int,
    db: AsyncSession,
    request: RecommendationRequest
) -> List[RecommendationResponse]:
    """
    Enrich recommendation results with TMDB data and streaming info
    """
    if not results:
        return []
        
    movie_ids = [r["movie_id"] for r in results]
    
    # Fetch movies from DB
    stmt = select(Movie).where(Movie.id.in_(movie_ids))
    db_movies = await db.execute(stmt)
    movies_map = {m.id: m for m in db_movies.scalars().all()}
    
    # Fetch streaming providers if requested
    providers_map = {}
    if request.streaming_providers or request.country_code:
        tmdb = TMDBClient()
        provider_service = ProviderService(db, tmdb)
        # We need TMDB IDs for provider lookup
        tmdb_ids = [m.tmdb_id for m in movies_map.values()]
        # This map is by internal ID or TMDB ID? ProviderService uses internal ID usually if we pass it?
        # ProviderService.get_providers_batch takes internal IDs.
        providers_map = await provider_service.get_providers_batch(movie_ids, request.country_code or "ES")
        await tmdb.close()

    recommendations = []
    allowed_providers = set(request.streaming_providers) if request.streaming_providers else None
    
    for result in results:
        movie = movies_map.get(result["movie_id"])
        if not movie:
            continue
            
        # Check streaming availability
        streaming_available = False
        streaming_providers = []
        
        if movie.id in providers_map:
            providers = providers_map[movie.id]
            streaming_providers = [p["provider_name"] for p in providers]
            
            if allowed_providers:
                # Check if any of the available providers are in the allowed list
                # We need provider IDs for strict matching
                available_ids = {p["provider_id"] for p in providers}
                if not allowed_providers.isdisjoint(available_ids):
                    streaming_available = True
            else:
                streaming_available = bool(providers)
        
        # Filter by streaming if requested
        if request.streaming_providers and not streaming_available:
            continue

        # Filter by VectorBox Score if min_rating is requested
        if request.min_rating and (movie.vectorbox_score is None or movie.vectorbox_score < request.min_rating):
            continue
        
        # Normalize score
        min_sim = 0.2
        max_sim = 0.7
        score = result["score"]
        
        if score > max_sim:
            final_score = 90 + ((score - max_sim) * 100)
            final_score = min(99, final_score)
        else:
            normalized = (score - min_sim) / (max_sim - min_sim)
            normalized = max(0.0, min(1.0, normalized))
            final_score = 60 + (normalized * 30)

        recommendations.append(RecommendationResponse(
            movie=MovieMetadata(
                tmdb_id=movie.tmdb_id,
                title=movie.title,
                original_title=movie.original_title,
                year=movie.year,
                runtime=movie.runtime,
                genres=movie.genres or [],
                overview=movie.overview,
                poster_path=movie.poster_path,
                backdrop_path=movie.backdrop_path,
                vote_average=movie.vote_average,
                vectorbox_score=movie.vectorbox_score,
                imdb_rating=movie.imdb_rating,
                metacritic_rating=movie.metacritic_rating,
                rotten_tomatoes_rating=movie.rotten_tomatoes_rating,
                title_es=movie.title_es,
                overview_es=movie.overview_es
            ),
            similarity_score=round(final_score, 0),
            streaming_available=streaming_available,
            streaming_providers=streaming_providers,
            contributors=result.get("contributors", [])
        ))
        
    return recommendations


@router.post("/general", response_model=List[RecommendationResponse])
async def get_general_recommendations(
    request: RecommendationRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Get general movie recommendations based on user's taste profile
    """
    try:
        clustering = ClusteringService()
        
        # Build filters
        filters = {}
        if request.year_min:
            filters["year_min"] = request.year_min
        if request.year_max:
            filters["year_max"] = request.year_max
        if request.runtime_max:
            filters["max_runtime"] = request.runtime_max
        if request.genres:
            filters["include_genres"] = request.genres
        if request.min_vote_count:
            filters["min_vote_count"] = request.min_vote_count
        if request.min_rating:
            filters["min_vectorbox_score"] = request.min_rating
        if request.original_language:
            filters["original_language"] = request.original_language
        if request.include_keywords:
            filters["include_keywords"] = request.include_keywords
        if request.watchlist_only:
            filters["watchlist_only"] = True
        if request.streaming_providers:
            filters["streaming_providers"] = request.streaming_providers
        if request.country_code:
            filters["country_code"] = request.country_code
            
        results = await clustering.get_item_based_recommendations(
            user_id=request.user_id,
            db=db,
            filters=filters,
            limit=request.limit,
            include_low_quality=request.include_low_quality or False,
            page=request.page # Pagination
        )
        
        return await _enrich_recommendations(results, request.user_id, db, request)
        
    except Exception as e:
        logger.error(f"General recommendation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate recommendations")


@router.get("/clusters/{user_id}", response_model=List[ClusterInfo])
async def get_user_clusters(
    user_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Get user's taste clusters (moods)
    """
    result = await db.execute(
        select(UserCluster).where(UserCluster.user_id == user_id)
    )
    clusters = result.scalars().all()
    
    if not clusters:
        raise HTTPException(
            status_code=404,
            detail="No clusters found. Please upload your Letterboxd data first."
        )
    
    # Build response with sample movies
    cluster_infos = []
    for cluster in clusters:
        # Get sample movies
        sample_movies = []
        if cluster.sample_movie_ids:
            result = await db.execute(
                select(Movie).where(Movie.id.in_(cluster.sample_movie_ids[:3]))
            )
            movies = result.scalars().all()
            
            for movie in movies:
                sample_movies.append(MovieMetadata(
                    tmdb_id=movie.tmdb_id,
                    title=movie.title,
                    original_title=movie.original_title,
                    year=movie.year,
                    runtime=movie.runtime,
                    genres=movie.genres or [],
                    overview=movie.overview,
                    poster_path=movie.poster_path,
                    backdrop_path=movie.backdrop_path,
                    vote_average=movie.vote_average
                ))
        
        cluster_infos.append(ClusterInfo(
            cluster_id=cluster.cluster_id,
            label=cluster.cluster_label,
            movie_count=cluster.movie_count,
            avg_rating=cluster.avg_rating,
            dominant_genres=cluster.dominant_genres or [],
            sample_movies=sample_movies
        ))
    
    return cluster_infos


@router.post("/by-mood", response_model=List[RecommendationResponse])
async def get_recommendations_by_mood(
    request: RecommendationRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Get movie recommendations for a specific mood (cluster)
    """
    if request.cluster_id is None:
        raise HTTPException(status_code=400, detail="cluster_id is required")
    
    try:
        clustering = ClusteringService()
        
        # Build filters
        filters = {}
        if request.year_min:
            filters["year_min"] = request.year_min
        if request.year_max:
            filters["year_max"] = request.year_max
        if request.runtime_max:
            filters["max_runtime"] = request.runtime_max
        if request.genres:
            filters["include_genres"] = request.genres
        if request.min_vote_count:
            filters["min_vote_count"] = request.min_vote_count
        if request.min_rating:
            filters["min_vectorbox_score"] = request.min_rating
        if request.original_language:
            filters["original_language"] = request.original_language
        if request.include_keywords:
            filters["include_keywords"] = request.include_keywords
        if request.watchlist_only:
            filters["watchlist_only"] = True
        if request.streaming_providers:
            filters["streaming_providers"] = request.streaming_providers
        if request.country_code:
            filters["country_code"] = request.country_code
        
        # Get recommendations
        results = await clustering.get_cluster_recommendations(
            user_id=request.user_id,
            cluster_id=request.cluster_id,
            db=db,
            filters=filters,
            limit=request.limit,
            page=request.page # Pagination
        )
        
        return await _enrich_recommendations(results, request.user_id, db, request)
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Mood recommendation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate recommendations")


@router.post("/random", response_model=RecommendationResponse)
async def get_random_recommendation(
    request: RecommendationRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Get a single random movie recommendation
    """
    try:
        # Reuse general recommendations logic but pick one
        clustering = ClusteringService()
        filters = {}
        # ... (simplified filter building)
        if request.genres: filters["include_genres"] = request.genres
        
        results = await clustering.get_item_based_recommendations(
            user_id=request.user_id,
            db=db,
            filters=filters,
            limit=50 # Get a pool
        )
        
        if not results:
             raise HTTPException(status_code=404, detail="No movies found matching criteria")
             
        enriched = await _enrich_recommendations(results, request.user_id, db, request)
        if not enriched:
            raise HTTPException(status_code=404, detail="No movies found matching criteria")
            
        return random.choice(enriched)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Random picker failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to pick random movie")


@router.post("/group", response_model=List[RecommendationResponse])
async def get_group_recommendations(
    request: GroupRecommendationRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Get recommendations for a group of users.
    """
    try:
        # 1. Find Watchlist Intersection
        watchlist_movies = {}
        for user_id in request.user_ids:
            result = await db.execute(
                select(UserRating.movie_id)
                .where((UserRating.user_id == user_id) & (UserRating.is_watchlist == True))
            )
            user_watchlist = set(result.scalars().all())
            for movie_id in user_watchlist:
                watchlist_movies[movie_id] = watchlist_movies.get(movie_id, 0) + 1
        
        threshold = len(request.user_ids) if len(request.user_ids) <= 2 else len(request.user_ids) / 2
        intersection_ids = [mid for mid, count in watchlist_movies.items() if count >= threshold]
        
        recommendations = []
        if intersection_ids:
            result = await db.execute(select(Movie).where(Movie.id.in_(intersection_ids)))
            movies = result.scalars().all()
            raw_results = [{"movie_id": m.id, "score": 1.0} for m in movies]
            
            enrich_req = RecommendationRequest(
                user_id=request.user_ids[0],
                limit=request.limit
            )
            recommendations = await _enrich_recommendations(raw_results, request.user_ids[0], db, enrich_req)
            
        # 2. Fallback
        if len(recommendations) < 5:
            remaining_limit = request.limit - len(recommendations)
            clustering = ClusteringService()
            general_results = await clustering.get_item_based_recommendations(
                user_id=request.user_ids[0],
                db=db,
                limit=remaining_limit
            )
            
            enrich_req = RecommendationRequest(user_id=request.user_ids[0], limit=remaining_limit)
            general_recs = await _enrich_recommendations(general_results, request.user_ids[0], db, enrich_req)
            
            existing_ids = {r.movie.tmdb_id for r in recommendations}
            for rec in general_recs:
                if rec.movie.tmdb_id not in existing_ids:
                    recommendations.append(rec)
                    
        return recommendations[:request.limit]

    except Exception as e:
        logger.error(f"Group recommendation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate group recommendations")


@router.get("/feed", response_model=FeedResponse)
async def get_feed(
    user_id: int,
    scope: str = "global",
    country_code: str = "ES",
    streaming_providers: str = "",
    include_low_quality: bool = False,
    db: AsyncSession = Depends(get_db)
):
    """
    Get Netflix-style multi-strategy recommendation feed.
    """
    try:
        provider_ids = []
        if streaming_providers:
            provider_ids = [int(x) for x in streaming_providers.split(",") if x.strip()]
        
        tmdb = TMDBClient()
        feed_service = FeedService()
        provider_service = ProviderService(db, tmdb)
        
        try:
            if scope == "watchlist":
                return await feed_service.get_watchlist_feed(user_id, db, tmdb, country_code, provider_ids)

            qdrant = QdrantService()
            
            # Parallel Execution
            tasks = [
                feed_service.get_popular_on_letterboxd_section(user_id, db, tmdb, country_code, provider_service),
                feed_service.get_because_you_watched_section(user_id, db, tmdb, qdrant, set(), country_code, provider_service),
                feed_service.get_your_taste_section(user_id, db, tmdb, set(), country_code, provider_service),
                feed_service.get_wildcard_section(user_id, db, tmdb, set(), country_code, provider_service),
                feed_service.get_random_recommendations_section(user_id, db, tmdb, set(), country_code, provider_service),
                feed_service.get_hidden_gems_section(user_id, db, tmdb, set(), country_code, provider_service),
                feed_service.get_available_now_section(user_id, db, tmdb, set(), country_code, provider_ids)
            ]
            
            results = await asyncio.gather(*tasks)
            section_popular, section_a, section_b, section_wildcard, section_random, section_c, section_d = results
            
            # Deduplicate
            seen_ids = set()
            final_sections = []
            ordered_results = [section_popular, section_a, section_b, section_wildcard, section_random, section_c, section_d]
            
            for section in ordered_results:
                if not section or not section.items:
                    continue
                unique_items = []
                for item in section.items:
                    if item.id not in seen_ids:
                        unique_items.append(item)
                        seen_ids.add(item.id)
                if unique_items:
                    section.items = unique_items
                    final_sections.append(section)
            
            # Deep Dive
            section_deep_dive = await feed_service.get_deep_dive_section(
                user_id, db, tmdb, seen_ids, country_code, provider_service, include_low_quality=include_low_quality
            )
            if section_deep_dive and section_deep_dive.items:
                insert_pos = min(len(final_sections), 4)
                final_sections.insert(insert_pos, section_deep_dive)

            return FeedResponse(feed=final_sections)
            
        finally:
            await tmdb.close()
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Feed generation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate feed")


@router.get("/watchlist")
async def get_watchlist(
    user_id: int,
    page: int = 1,
    limit: int = 20,
    country_code: str = "ES",
    sort_by: str = "date_added",
    runtime_min: Optional[int] = None,
    runtime_max: Optional[int] = None,
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    genres: Optional[str] = None,
    min_rating: Optional[float] = None,
    streaming_providers: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Get filtered watchlist items for grid view.
    """
    tmdb = TMDBClient()
    feed_service = FeedService()
    try:
        stmt = (
            select(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                UserRating.is_watchlist.is_(True),
                UserRating.is_watched.is_(False)
            )
        )

        if runtime_min: stmt = stmt.where(Movie.runtime >= runtime_min)
        if runtime_max: stmt = stmt.where(Movie.runtime <= runtime_max)
        if year_min: stmt = stmt.where(Movie.year >= year_min)
        if year_max: stmt = stmt.where(Movie.year <= year_max)
        if min_rating: stmt = stmt.where(Movie.vectorbox_score >= min_rating)
        
        # Fetch all candidates first (for memory filtering)
        # Note: If no memory filters, we could paginate here.
        # But for consistency, we fetch all matching DB filters.
        result = await db.execute(stmt)
        movies = result.scalars().all()
        
        filtered_movies = []
        provider_ids = []
        if streaming_providers:
            provider_ids = [int(x) for x in streaming_providers.split(",") if x.strip()]

        for movie in movies:
            if genres:
                movie_genres = [g.lower() for g in movie.genres] if movie.genres else []
                if not any(g.lower() in movie_genres for g in genres.split(",")):
                    continue
            filtered_movies.append(movie)

        # Streaming Filter & Pagination
        final_items = []
        
        # Calculate pagination slice
        start = (page - 1) * limit
        end = start + limit
        
        # We need to filter by streaming providers BEFORE pagination if requested
        # This is expensive as we need to fetch providers for ALL candidates
        # Optimization: Only fetch providers for the current page IF no streaming filter?
        # But if streaming filter is ON, we MUST check all.
        
        provider_service = ProviderService(db, tmdb)
        
        if provider_ids:
            # Hard case: Must check availability for all candidates to find the ones for this page
            # Limit to first 500 to avoid timeout
            candidates = filtered_movies[:500] 
            movie_ids = [m.id for m in candidates]
            providers_map = await provider_service.get_providers_batch(movie_ids, country_code)
            
            available_movies = []
            for movie in candidates:
                movie_providers = providers_map.get(movie.id, [])
                has_provider = False
                for p in movie_providers:
                    if p["provider_id"] in provider_ids:
                        has_provider = True
                        break
                if has_provider:
                    available_movies.append((movie, [p["provider_name"] for p in movie_providers]))
            
            # Sort
            if sort_by == "date_added": available_movies.sort(key=lambda x: x[0].id, reverse=True)
            elif sort_by == "title": available_movies.sort(key=lambda x: x[0].title)
            elif sort_by == "rating": available_movies.sort(key=lambda x: x[0].vectorbox_score or 0, reverse=True)
            
            # Paginate
            paginated = available_movies[start:end]
            
            for movie, providers in paginated:
                item = await feed_service.create_feed_item(movie, 1.0, country_code, tmdb, streaming_providers=providers)
                final_items.append(item)
                
            total_items = len(available_movies)
            
        else:
            # Easy case: Sort then Paginate then Fetch Providers for just the page
            if sort_by == "date_added": filtered_movies.sort(key=lambda x: x.id, reverse=True)
            elif sort_by == "title": filtered_movies.sort(key=lambda x: x.title)
            elif sort_by == "rating": filtered_movies.sort(key=lambda x: x.vectorbox_score or 0, reverse=True)
            
            paginated_movies = filtered_movies[start:end]
            
            # Fetch providers for just this page
            movie_ids = [m.id for m in paginated_movies]
            providers_map = await provider_service.get_providers_batch(movie_ids, country_code)
            
            for movie in paginated_movies:
                movie_providers = providers_map.get(movie.id, [])
                flat_providers = [p["provider_name"] for p in movie_providers]
                item = await feed_service.create_feed_item(movie, 1.0, country_code, tmdb, streaming_providers=flat_providers)
                final_items.append(item)
                
            total_items = len(filtered_movies)

        return {"items": final_items, "total": total_items, "page": page, "limit": limit}

    finally:
        await tmdb.close()


@router.get("/random-row", response_model=FeedSection)
async def get_random_row(
    user_id: int,
    country_code: str = "ES",
    scope: str = "global",
    db: AsyncSession = Depends(get_db)
):
    """
    Get a fresh set of random recommendations (Reroll functionality).
    """
    tmdb = TMDBClient()
    feed_service = FeedService()
    try:
        if scope == "watchlist":
            stmt = (
                select(Movie)
                .join(UserRating, Movie.id == UserRating.movie_id)
                .where(
                    UserRating.user_id == user_id,
                    UserRating.is_watchlist.is_(True),
                    UserRating.is_watched.is_(False)
                )
            )
            result = await db.execute(stmt)
            watchlist_movies = result.scalars().all()
            
            if not watchlist_movies:
                raise HTTPException(status_code=404, detail="Watchlist empty")
                
            random_movies = list(watchlist_movies)
            random.shuffle(random_movies)
            
            items = []
            for movie in random_movies[:10]:
                 item = await feed_service.create_feed_item(movie, 0.85, country_code, tmdb)
                 items.append(item)
                 
            return FeedSection(
                id="random_watchlist",
                title="Shuffle: From Your Watchlist",
                type="watchlist_random",
                items=items
            )
        else:
            section = await feed_service.get_random_recommendations_section(user_id, db, tmdb, set(), country_code)
            if not section:
                raise HTTPException(status_code=404, detail="Could not generate random recommendations")
            return section
    finally:
        await tmdb.close()


@router.get("/hidden-gems", response_model=FeedSection)
async def get_hidden_gems_row(
    user_id: int,
    country_code: str = "ES",
    db: AsyncSession = Depends(get_db)
):
    """
    Get a fresh set of hidden gems (Reroll functionality).
    """
    tmdb = TMDBClient()
    feed_service = FeedService()
    try:
        clustering = ClusteringService()
        results = await clustering.get_general_recommendations(
            user_id=user_id,
            db=db,
            filters={"min_vote_count": 50, "min_rating": 5.0},
            limit=2000
        )
        
        if not results:
            raise HTTPException(status_code=404, detail="No recommendations found")
            
        random.shuffle(results)
        
        items = []
        seen_ids = set()
        
        for res in results:
            movie_id = res["movie_id"]
            if movie_id in seen_ids:
                continue
            
            movie_result = await db.execute(select(Movie).where(Movie.id == movie_id))
            movie = movie_result.scalar_one_or_none()
            
            if movie and movie.vote_average and movie.vote_average > 7.0:
                if movie.vote_count and movie.vote_count < 50:
                     continue

                item = await feed_service.create_feed_item(movie, res["score"], country_code, tmdb)
                items.append(item)
                seen_ids.add(movie_id)
                
                if len(items) >= 10:
                    break
        
        if not items:
             raise HTTPException(status_code=404, detail="No hidden gems found")

        return FeedSection(
            id="hidden_gems",
            title="Hidden Gems",
            items=items
        )
    finally:
        await tmdb.close()
