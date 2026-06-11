from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, or_, func
from typing import List, Optional, Set, Dict
import random
import logging
import asyncio

from config import get_db, AsyncSessionLocal
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
from dependencies import get_tmdb_client, get_qdrant_service, get_current_user, get_current_or_anonymous_user, get_embedding_service, get_redis
from limiter import limiter
from models.schemas import TokenResponse
from services.embedding_service import EmbeddingService
from utils.scoring import normalize_similarity_score

router = APIRouter(
    tags=["recommendations"]
)

logger = logging.getLogger(__name__)


async def _invalidate_user_feed_cache(user_id: int) -> None:
    """Sweep section, signal, and rotation cache keys for a user. Mirrors rss._invalidate_feed_cache.

    Matches the canonical pattern in rss.py so any web action that mutates a user's
    rating state (reject / watched) wipes the same surfaces an RSS sync would.
    """
    try:
        import os
        import redis.asyncio as aioredis
        from config import FEED_CACHE_VERSION
        from services.cache_service import scan_and_delete
        r = aioredis.from_url(
            os.environ.get("REDIS_URL", "redis://redis:6379"),
            decode_responses=True,
        )
        try:
            for pattern in (
                f"section:{FEED_CACHE_VERSION}:{user_id}:*",
                f"signal_cache:{user_id}:*",
            ):
                await scan_and_delete(r, pattern)
            await r.delete(f"cluster_rotation:{FEED_CACHE_VERSION}:{user_id}")
        finally:
            await r.close()
    except Exception as e:
        logger.warning(f"Feed cache invalidation failed for user_id={user_id}: {e}")

async def _enrich_recommendations(
    results: List[Dict],
    user_id: int,
    db: AsyncSession,
    request: RecommendationRequest,
    tmdb: TMDBClient
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
        # tmdb is passed in
        provider_service = ProviderService(db, tmdb)
        # We need TMDB IDs for provider lookup
        # ProviderService.get_providers_batch takes internal IDs.
        providers_map = await provider_service.get_providers_batch(movie_ids, request.country_code or "ES")

    recommendations = []
    allowed_providers = set(request.streaming_providers) if request.streaming_providers else None
    
    for result in results:
        movie = movies_map.get(result["movie_id"])
        # Drop Debugging
        if not movie:
            logger.warning(f"Enrichment: Movie {result['movie_id']} not found in DB map.")
            continue
            
        # Check streaming availability
        streaming_available = False
        # Reset per iteration: get_providers_batch omits movies whose TMDB
        # provider fetch returned None (films with no providers anywhere), so
        # a missing entry would otherwise raise UnboundLocalError on the first
        # movie or leak the previous movie's provider list into this one.
        streaming_providers = []
        if movie.id in providers_map:
             # ...
             # (reconstruct existing logic roughly)
             providers = providers_map[movie.id]
             streaming_providers = [p["provider_name"] for p in providers]
             
             if allowed_providers:
                available_ids = {p["provider_id"] for p in providers}
                if not allowed_providers.isdisjoint(available_ids):
                    streaming_available = True
             else:
                streaming_available = bool(providers)

        # Filter by streaming if requested
        if request.streaming_providers and not streaming_available:
            logger.info(f"Dropped {movie.title}: Streaming unmatched (Required: {request.streaming_providers})")
            continue

        # Filter by VectorBox Score if min_rating is requested
        # We treat None as 50 (neutral) to avoid dropping movies just because OMDb data is missing
        stats_score = movie.vectorbox_score if movie.vectorbox_score is not None else 50
        
        # DEBUG: Log the comparison
        if request.min_rating:
             if stats_score < request.min_rating:
                 logger.info(f"Dropped {movie.title}: Score {stats_score} < Min {request.min_rating}")
                 continue
        
        final_score = normalize_similarity_score(result["score"])

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
@limiter.limit("30/minute")
async def get_general_recommendations(
    http_request: Request,
    request: RecommendationRequest,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service)
):
    """
    Get general movie recommendations based on user's taste profile
    """
    try:
        # L-1: user_id always derived from JWT (no more request.user_id)
        user_id = current_user.user_id
        
        clustering = ClusteringService(qdrant=qdrant)
        
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
            user_id=user_id,
            db=db,
            filters=filters,
            limit=request.limit,
            page=request.page # Pagination
        )
        
        return await _enrich_recommendations(results, user_id, db, request, tmdb)
        
    except Exception as e:
        logger.error(f"General recommendation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate recommendations")


@router.get("/clusters/{user_id}", response_model=List[ClusterInfo])
async def get_user_clusters(
    user_id: int,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get user's taste clusters (moods)
    """
    # IDOR Check
    if current_user.user_id != user_id:
         raise HTTPException(status_code=403, detail="Access Denied")

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
@limiter.limit("30/minute")
async def get_recommendations_by_mood(
    http_request: Request,
    request: RecommendationRequest,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service)
):
    """
    Get movie recommendations for a specific mood (cluster)
    """
    if request.cluster_id is None:
        raise HTTPException(status_code=400, detail="cluster_id is required")
    
    # IDOR Protection
    # L-1: user_id always derived from JWT
    user_id = current_user.user_id

    try:
        clustering = ClusteringService(qdrant=qdrant)
        
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
            user_id=user_id,
            db=db,
            cluster_id=request.cluster_id,
            filters=filters,
            limit=request.limit,
            page=request.page # Pagination
        )
        
        return await _enrich_recommendations(results, user_id, db, request, tmdb)
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Mood recommendation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate recommendations")


@router.post("/random", response_model=RecommendationResponse)
@limiter.limit("30/minute")
async def get_random_recommendation(
    http_request: Request,
    request: RecommendationRequest,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service)
):
    """
    Get a single random movie recommendation
    """
    try:
        # IDOR Protection
        # L-1: user_id always derived from JWT
        user_id = current_user.user_id
        
        # Reuse general recommendations logic but pick one
        clustering = ClusteringService(qdrant=qdrant)
        filters = {}
        # ... (simplified filter building)
        if request.genres: filters["include_genres"] = request.genres
        
        results = await clustering.get_item_based_recommendations(
            user_id=user_id,
            db=db,
            filters=filters,
            limit=50 # Get a pool
        )

        if not results:
             raise HTTPException(status_code=404, detail="No movies found matching criteria")

        enriched = await _enrich_recommendations(results, user_id, db, request, tmdb)
        if not enriched:
            raise HTTPException(status_code=404, detail="No movies found matching criteria")
            
        return random.choice(enriched)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Random picker failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to pick random movie")


@router.post("/group", response_model=List[RecommendationResponse])
@limiter.limit("10/minute")
async def get_group_recommendations(
    http_request: Request,  # required by slowapi
    request: GroupRecommendationRequest,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service)
):
    """
    Get recommendations for a group of users.
    Requester must be a member of the group — otherwise an unauthenticated
    guest could enumerate any pair of users' watchlist intersections.
    """
    if current_user.user_id not in request.user_ids:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        # 1. Find Watchlist Intersection — batch query instead of per-user loop
        result = await db.execute(
            select(UserRating.movie_id, UserRating.user_id).where(
                UserRating.user_id.in_(request.user_ids),
                UserRating.is_watchlist.is_(True)
            )
        )
        watchlist_movies: dict = {}
        for movie_id, user_id in result.all():
            watchlist_movies[movie_id] = watchlist_movies.get(movie_id, 0) + 1
        
        threshold = len(request.user_ids) if len(request.user_ids) <= 2 else len(request.user_ids) / 2
        intersection_ids = [mid for mid, count in watchlist_movies.items() if count >= threshold]
        
        recommendations = []
        if intersection_ids:
            result = await db.execute(select(Movie).where(Movie.id.in_(intersection_ids)))
            movies = result.scalars().all()
            raw_results = [{"movie_id": m.id, "score": 1.0} for m in movies]
            
            enrich_req = RecommendationRequest(
                limit=request.limit
            )
            recommendations = await _enrich_recommendations(raw_results, request.user_ids[0], db, enrich_req, tmdb)
            
        # 2. Fallback
        if len(recommendations) < 5:
            remaining_limit = request.limit - len(recommendations)
            clustering = ClusteringService(qdrant=qdrant)
            general_results = await clustering.get_item_based_recommendations(
                user_id=request.user_ids[0],
                db=db,
                limit=remaining_limit
            )
            
            enrich_req = RecommendationRequest(limit=remaining_limit)
            general_recs = await _enrich_recommendations(general_results, request.user_ids[0], db, enrich_req, tmdb)
            
            existing_ids = {r.movie.tmdb_id for r in recommendations}
            for rec in general_recs:
                if rec.movie.tmdb_id not in existing_ids:
                    recommendations.append(rec)
                    
        return recommendations[:request.limit]

    except Exception as e:
        logger.error(f"Group recommendation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate group recommendations")


@router.get("/feed", response_model=FeedResponse)
@limiter.limit("20/minute")
async def get_feed(
    request: Request,
    current_user: TokenResponse = Depends(get_current_or_anonymous_user),
    scope: str = "global",
    country_code: str = "ES",
    streaming_providers: str = "",
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding: EmbeddingService = Depends(get_embedding_service),
    redis=Depends(get_redis),
    background_tasks: BackgroundTasks = None
):
    """
    Get Netflix-style multi-strategy recommendation feed.
    """
    user_id = current_user.user_id

    try:
        provider_ids = []
        if streaming_providers:
            provider_ids = [int(x) for x in streaming_providers.split(",") if x.strip()]
        
        # Decision (2026-05-17): users with sub-clustering ratings (e.g. just
        # migrated from a guest with 3 rated films, or a fresh ZIP that's
        # still enriching in background) should NOT see "data incomplete".
        # The feed pipeline already gracefully degrades — personalized
        # sections (BYW, Picked For You, Cult Actor) return None when their
        # input signals are too sparse, and we filter those out client-side.
        # Non-personalized sections (Hidden Gems, Niche Picks, Upcoming,
        # Random Picks, Popular on Letterboxd) work fine without clusters.
        # Old "data incomplete" gate forced a ZIP/onboarding wall on every
        # sub-threshold user — same friction as the guest cap we already
        # removed.

        # Services are now injected
        feed_service = FeedService(qdrant=qdrant, embedding_service=embedding)
        
        if scope == "watchlist":
            return await feed_service.get_watchlist_feed(user_id, db, tmdb, country_code, provider_ids)
        
        # Delegate parallel execution to the service
        return await feed_service.get_main_feed(
            user_id=user_id,
            country_code=country_code,
            streaming_providers=provider_ids,
            tmdb=tmdb,
            qdrant=qdrant,
            background_tasks=background_tasks,
            redis_client=redis,
        )
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Feed generation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate feed")


@router.get("/watchlist")
@limiter.limit("20/minute")
async def get_watchlist(
    request: Request,
    current_user: TokenResponse = Depends(get_current_user),
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
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding: EmbeddingService = Depends(get_embedding_service)
):
    """
    Get filtered watchlist items for grid view.
    """
    user_id = current_user.user_id
    
    feed_service = FeedService(qdrant=qdrant, embedding_service=embedding)

    stmt = (
        select(Movie)
        .join(UserRating, Movie.id == UserRating.movie_id)
        .where(
            UserRating.user_id == user_id,
            UserRating.is_watchlist.is_(True),
            UserRating.is_watched.is_(False)
        )
    )

    # Push scalar filters to DB
    if runtime_min: stmt = stmt.where(Movie.runtime >= runtime_min)
    if runtime_max: stmt = stmt.where(Movie.runtime <= runtime_max)
    if year_min: stmt = stmt.where(Movie.year >= year_min)
    if year_max: stmt = stmt.where(Movie.year <= year_max)
    if min_rating: stmt = stmt.where(Movie.vectorbox_score >= min_rating)

    # Push genre filter to DB using PostgreSQL array overlap
    if genres:
        genre_list = [g.strip() for g in genres.split(",")]
        stmt = stmt.where(Movie.genres.overlap(genre_list))

    # Push sort to DB
    if sort_by == "date_added":
        stmt = stmt.order_by(Movie.id.desc())
    elif sort_by == "title":
        stmt = stmt.order_by(Movie.title)
    elif sort_by == "rating":
        stmt = stmt.order_by(Movie.vectorbox_score.desc().nulls_last())

    final_items = []
    provider_service = ProviderService(db, tmdb)

    provider_ids = []
    if streaming_providers:
        provider_ids = [int(x) for x in streaming_providers.split(",") if x.strip()]

    if provider_ids:
        # Streaming path: load bounded candidates, filter by provider, paginate in Python
        result = await db.execute(stmt.limit(500))
        candidates = result.scalars().all()

        providers_map = await provider_service.get_providers_batch([m.id for m in candidates], country_code)

        available_movies = []
        for movie in candidates:
            movie_providers = providers_map.get(movie.id, [])
            if any(p["provider_id"] in provider_ids for p in movie_providers):
                flat_providers = [p["provider_name"] for p in movie_providers]
                available_movies.append((movie, flat_providers))

        total_items = len(available_movies)
        start = (page - 1) * limit
        paginated = available_movies[start:start + limit]

        for movie, providers in paginated:
            item = await feed_service.engine.create_feed_item(movie, 1.0, country_code, tmdb, streaming_providers=providers)
            final_items.append(item)

    else:
        # No streaming filter: count + DB-level pagination
        count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
        total_items = count_result.scalar_one()

        paginated_stmt = stmt.limit(limit).offset((page - 1) * limit)
        result = await db.execute(paginated_stmt)
        paginated_movies = result.scalars().all()

        movie_ids = [m.id for m in paginated_movies]
        providers_map = await provider_service.get_providers_batch(movie_ids, country_code)

        for movie in paginated_movies:
            movie_providers = providers_map.get(movie.id, [])
            flat_providers = [p["provider_name"] for p in movie_providers]
            item = await feed_service.engine.create_feed_item(movie, 1.0, country_code, tmdb, streaming_providers=flat_providers)
            final_items.append(item)

    return {"items": final_items, "total": total_items, "page": page, "limit": limit}



@router.get("/random-row", response_model=FeedSection)
@limiter.limit("20/minute")
async def get_random_row(
    request: Request,
    current_user: TokenResponse = Depends(get_current_user),
    country_code: str = "ES",
    scope: str = "global",
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding: EmbeddingService = Depends(get_embedding_service)
):
    """
    Get a fresh set of random recommendations (Reroll functionality).
    """
    user_id = current_user.user_id
    
    feed_service = FeedService(qdrant=qdrant, embedding_service=embedding)
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
            
            selected_movies = random_movies[:10]
            
            # Batch fetch providers
            movie_ids = [m.id for m in selected_movies]
            provider_service = ProviderService(db, tmdb)
            providers_map = await provider_service.get_providers_batch(movie_ids, country_code)
            
            items = []
            for movie in selected_movies:
                 providers_data = providers_map.get(movie.id, [])
                 provider_names = [p["provider_name"] for p in providers_data]
                 
                 item = await feed_service.engine.create_feed_item(
                     movie=movie, 
                     score=0.85, 
                     country=country_code, 
                     tmdb=tmdb,
                     streaming_providers=provider_names
                 )
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
    except Exception as e:
        logger.error(f"Random row failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate random recommendations")


@router.get("/hidden-gems", response_model=FeedSection)
@limiter.limit("20/minute")
async def get_hidden_gems_row(
    request: Request,
    current_user: TokenResponse = Depends(get_current_user),
    country_code: str = "ES",
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding: EmbeddingService = Depends(get_embedding_service)
):
    """
    Get a fresh set of hidden gems (Reroll functionality).
    """
    user_id = current_user.user_id
    feed_service = FeedService(qdrant=qdrant, embedding_service=embedding)
    try:
        clustering = ClusteringService(qdrant=qdrant)
        results = await clustering.get_user_centric_recommendations(
            user_id=user_id,
            db=db,
            filters={"min_vote_count": 50, "min_rating": 5.0},
            limit=2000
        )
        
        if not results:
            raise HTTPException(status_code=404, detail="No recommendations found")
            
        random.shuffle(results)
        
        # Optimization: Process in batches to avoid N+1
        # Take a sufficient slice to ensure we find 10 valid items
        # We process 100 candidates to ensure we find 10 matches after filtering
        candidates = results[:100] 
        candidate_ids = [res["movie_id"] for res in candidates]
        
        # Batch fetch movies
        stmt = select(Movie).where(Movie.id.in_(candidate_ids))
        movie_result = await db.execute(stmt)
        movies_map = {m.id: m for m in movie_result.scalars().all()}
        
        valid_movies = []
        scores_map = {}
        
        # Filter candidates in memory
        for res in candidates:
            movie_id = res["movie_id"]
            movie = movies_map.get(movie_id)
            
            if not movie:
                continue
                
            if movie.vote_average and movie.vote_average > 7.0:
                if movie.vote_count and movie.vote_count < 50:
                     continue
                
                valid_movies.append(movie)
                scores_map[movie.id] = res["score"]
                
                if len(valid_movies) >= 10:
                    break
        
        if not valid_movies:
             # Fallback if strict filters eliminate everyone (unlikely with 100 pool)
             raise HTTPException(status_code=404, detail="No hidden gems found")

        # Batch fetch providers
        valid_ids = [m.id for m in valid_movies]
        provider_service = ProviderService(db, tmdb)
        providers_map = await provider_service.get_providers_batch(valid_ids, country_code)

        items = []
        for movie in valid_movies:
            providers_data = providers_map.get(movie.id, [])
            provider_names = [p["provider_name"] for p in providers_data]
            
            item = await feed_service.engine.create_feed_item(
                movie=movie, 
                score=scores_map[movie.id], 
                country=country_code, 
                tmdb=tmdb,
                streaming_providers=provider_names
            )
            items.append(item)

        return FeedSection(
            id="hidden_gems",
            title="Hidden Gems",
            items=items
        )
    except Exception as e:
        logger.error(f"Hidden gems failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate hidden gems")


@router.post("/reject/{tmdb_id}")
@limiter.limit("60/minute")
async def reject_movie(
    request: Request,
    tmdb_id: int,
    background_tasks: BackgroundTasks,
    current_user: TokenResponse = Depends(get_current_or_anonymous_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a movie as 'Not Interested'. Upserts UserRating with is_rejected=True.

    Cache invalidation runs as a background task — same pattern as
    mark_watched: keep the response fast so rapid-fire clicks don't
    stack the SCAN+DELETE inside the request path.
    """
    user_id = current_user.user_id

    # Find the internal movie by tmdb_id
    movie_result = await db.execute(
        select(Movie).where(Movie.tmdb_id == tmdb_id)
    )
    movie = movie_result.scalar_one_or_none()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    # CONC-1 parity with /onboarding/rate: atomic INSERT … ON CONFLICT so two
    # concurrent rejects (double-click) can't both pass a "not exists" check
    # and collide on the user/movie unique index → 500. On conflict only
    # is_rejected flips; the rest of the row is preserved.
    await db.execute(
        pg_insert(UserRating)
        .values(
            user_id=user_id,
            movie_id=movie.id,
            is_rejected=True,
            is_watched=False,
        )
        .on_conflict_do_update(
            index_elements=[UserRating.user_id, UserRating.movie_id],
            set_={"is_rejected": True},
        )
    )
    await db.commit()

    background_tasks.add_task(_invalidate_user_feed_cache, user_id)

    return {"status": "ok", "tmdb_id": tmdb_id, "rejected": True}


@router.get("/movies/rejected")
async def get_rejected_movies(
    current_user: TokenResponse = Depends(get_current_or_anonymous_user),
    db: AsyncSession = Depends(get_db),
):
    """List all movies the user has marked as 'Not Interested'."""
    user_id = current_user.user_id

    result = await db.execute(
        select(Movie.tmdb_id, Movie.title, Movie.year, Movie.poster_path)
        .join(UserRating, Movie.id == UserRating.movie_id)
        .where(
            UserRating.user_id == user_id,
            UserRating.is_rejected.is_(True),
        )
        .order_by(UserRating.created_at.desc())
    )
    rows = result.all()

    return [
        {
            "tmdb_id": row.tmdb_id,
            "title": row.title,
            "year": row.year,
            "poster_path": row.poster_path,
        }
        for row in rows
    ]


@router.delete("/movies/{tmdb_id}/reject")
@limiter.limit("60/minute")
async def unreject_movie(
    request: Request,
    tmdb_id: int,
    current_user: TokenResponse = Depends(get_current_or_anonymous_user),
    db: AsyncSession = Depends(get_db),
):
    """Undo a 'Not Interested' rejection."""
    user_id = current_user.user_id

    movie_result = await db.execute(
        select(Movie).where(Movie.tmdb_id == tmdb_id)
    )
    movie = movie_result.scalar_one_or_none()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    existing_result = await db.execute(
        select(UserRating).where(
            UserRating.user_id == user_id,
            UserRating.movie_id == movie.id,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if not existing or not existing.is_rejected:
        raise HTTPException(status_code=404, detail="Movie is not rejected")

    existing.is_rejected = False
    await db.commit()

    await _invalidate_user_feed_cache(user_id)

    return {"status": "ok", "tmdb_id": tmdb_id, "rejected": False}


@router.post("/movies/{tmdb_id}/watched")
@limiter.limit("60/minute")
async def mark_watched(
    request: Request,
    tmdb_id: int,
    background_tasks: BackgroundTasks,
    current_user: TokenResponse = Depends(get_current_or_anonymous_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a movie as watched from the web (no date or rewatch info available).

    Cache invalidation runs as a background task so the response returns
    immediately. Otherwise rapid-fire clicks ("watched 3 films in a row")
    were stacking the SCAN+DELETE inside the request path; each next click
    waited for the previous one to finish, and the feed refetch the
    frontend triggered after each click could land before the next commit
    propagated → user saw the just-watched film reappear in the feed.
    """
    user_id = current_user.user_id

    movie_result = await db.execute(
        select(Movie).where(Movie.tmdb_id == tmdb_id)
    )
    movie = movie_result.scalar_one_or_none()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    # CONC-1 parity with /onboarding/rate: atomic upsert. On conflict only
    # is_watched flips — an existing row keeps its real watch_count; the
    # insert path uses watch_count=0 ("marked from web" sentinel, see
    # _web_watches_query).
    await db.execute(
        pg_insert(UserRating)
        .values(
            user_id=user_id,
            movie_id=movie.id,
            is_watched=True,
            watch_count=0,
        )
        .on_conflict_do_update(
            index_elements=[UserRating.user_id, UserRating.movie_id],
            set_={"is_watched": True},
        )
    )
    await db.commit()

    background_tasks.add_task(_invalidate_user_feed_cache, user_id)

    return {"status": "ok", "tmdb_id": tmdb_id, "watched": True}


def _web_watches_query(user_id: int, *, ascending: bool):
    """Films the user marked watched on VectorBox (sentinel: `is_watched=true
    AND watch_count=0`) — set by `mark_watched` when the source is the web,
    not a Letterboxd ZIP import."""
    order = UserRating.created_at.asc() if ascending else UserRating.created_at.desc()
    return (
        select(
            Movie.tmdb_id, Movie.title, Movie.year,
            Movie.letterboxd_uri, Movie.poster_path,
            UserRating.created_at,
        )
        .join(UserRating, Movie.id == UserRating.movie_id)
        .where(UserRating.user_id == user_id)
        .where(UserRating.is_watched.is_(True))
        .where(UserRating.watch_count == 0)
        .order_by(order)
    )


@router.get("/movies/watched-on-web")
async def list_web_watches(
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """F-22: list films the user marked as watched directly on VectorBox.
    These never made it to Letterboxd because we can't write there; the UI
    uses this for manual reconciliation or to trigger the CSV export."""
    rows = (await db.execute(_web_watches_query(current_user.user_id, ascending=False))).all()
    return [
        {
            "tmdb_id": r.tmdb_id,
            "title": r.title,
            "year": r.year,
            "letterboxd_uri": r.letterboxd_uri,
            "poster_path": r.poster_path,
            "watched_date": r.created_at.date().isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def _csv_safe(value) -> str:
    """Defuse CSV/spreadsheet formula injection.

    Excel/Numbers/LibreOffice treat any cell starting with `=`, `+`, `-`,
    `@`, `\t`, or `\r` as a formula. A movie title like
    `=HYPERLINK("https://evil","ok")` would execute on open. Prefix the
    cell with a single quote — Excel renders it as text, never as a formula.
    """
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


@router.get("/movies/watched-on-web.csv")
async def export_web_watches_csv(
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """F-22: Letterboxd-import-compatible CSV of the user's web-marked
    watches. Columns: Letterboxd URI (preferred), Title, Year, WatchedDate.
    See https://letterboxd.com/about/importing-data/. Title+Year is the
    fallback match when URI is missing."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    user_id = current_user.user_id
    rows = (await db.execute(_web_watches_query(user_id, ascending=True))).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Letterboxd URI", "Title", "Year", "WatchedDate"])
    for r in rows:
        writer.writerow([
            _csv_safe(r.letterboxd_uri),
            _csv_safe(r.title),
            _csv_safe(r.year),
            _csv_safe(r.created_at.date().isoformat() if r.created_at else ""),
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="vectorbox-web-watches-{user_id}.csv"'
        },
    )


@router.post("/feed/reroll-cluster")
@limiter.limit("10/minute")
async def reroll_cluster(
    request: Request,
    current_user: TokenResponse = Depends(get_current_user),
):
    """Advance niche_theme_rotation and invalidate niche_picks cache."""
    user_id = current_user.user_id
    deleted = 0
    try:
        import redis.asyncio as aioredis
        import os
        from services.feed_service import FEED_CACHE_VERSION
        from services.recommendation_engine import GLOBAL_THEMES
        r = aioredis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379"),
            decode_responses=True,
        )
        try:
            from services.cache_service import scan_and_delete
            rotation_key = f"niche_theme_rotation:{FEED_CACHE_VERSION}:{user_id}"
            current = await r.get(rotation_key)
            n_themes = len(GLOBAL_THEMES)
            next_index = ((int(current) + 1) if current is not None else 1) % n_themes
            await r.setex(rotation_key, 60 * 60 * 24 * 7, str(next_index))

            deleted += await scan_and_delete(r, f"section:{FEED_CACHE_VERSION}:{user_id}:niche_picks:*")
            # Invalidate the full feed snapshot so the UI refetches sections
            await scan_and_delete(r, f"feed:{FEED_CACHE_VERSION}:{user_id}:*")
            logger.info(
                f"Niche theme reroll user {user_id}: theme → {next_index} "
                f"({GLOBAL_THEMES[next_index]['title']}), deleted {deleted} niche_picks keys"
            )
        finally:
            await r.close()
    except Exception as e:
        logger.warning(f"Niche theme reroll failed: {e}")

    return {"status": "ok", "message": "Niche theme will rotate on next feed load"}

