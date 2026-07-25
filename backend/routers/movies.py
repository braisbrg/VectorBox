import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, confloat, constr
from typing import Optional
from limiter import limiter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from datetime import datetime, timezone

from config import get_db, REDIS_URL
from models.database import Movie, UserRating
from dependencies import get_tmdb_client, get_current_user
from services.tmdb_client import TMDBClient
from services.movie_service import MovieService
from services.onboarding_service import maybe_complete_onboarding
from services.profile_cache import set_profile_dirty
from models.schemas import TokenResponse

router = APIRouter()
logger = logging.getLogger(__name__)

class RateMovieRequest(BaseModel):
    rating: Optional[confloat(ge=0, le=5)] = None
    is_watchlist: bool = False
    is_liked: bool = False

@router.get("/{tmdb_id}")
@limiter.limit("60/minute")
async def get_movie_details(
    # slowapi needs the starlette Request named `request` (F0 lesson).
    # This route is intentionally public (guest dossier), so the rate limit is
    # the ONLY control: a DB miss costs a TMDB call and a DB hit costs a
    # get_watch_providers call + a provider upsert, both driven by an
    # enumerable tmdb_id.
    request: Request,
    tmdb_id: int,
    # ISO 3166-1 alpha-2. Was a free-form str, which multiplied provider-cache
    # cardinality per film for anyone who felt like it.
    country: constr(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$") = "ES",
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client)
):
    """
    Fetch complete movie metadata by TMDB ID.
    Prioritizes local DB for VectorBox scores and rich metadata.
    """
    try:
        # 1. Try local DB
        stmt = select(Movie).where(Movie.tmdb_id == tmdb_id)
        result = await db.execute(stmt)
        movie = result.scalars().first()

        if movie:
            # availability chips for the dossier + quick-look (prototype "availability · {CC}")
            streaming_providers: list[str] = []
            try:
                from services.provider_service import ProviderService
                p_data = await ProviderService(db, tmdb).get_providers(movie.id, country)
                streaming_providers = [p["provider_name"] for p in p_data]
            except Exception:
                logger.warning(f"Provider lookup failed for movie {tmdb_id}", exc_info=True)
            return {
                "tmdb_id": movie.tmdb_id,
                "title": movie.title,
                "year": movie.year,
                "runtime": movie.runtime,
                "genres": movie.genres or [],
                "overview": movie.overview,
                "poster_url": movie.poster_path, # FeedItem expects poster_url
                "backdrop_path": movie.backdrop_path,
                "directors": movie.directors or [],
                "cast": movie.cast or [],
                "tagline": movie.tagline,
                "match_score": movie.vectorbox_score or 0,
                "vectorbox_score": movie.vectorbox_score,
                "imdb_rating": movie.imdb_rating,
                "metacritic_rating": movie.metacritic_rating,
                "title_es": movie.title_es,
                "overview_es": movie.overview_es,
                "streaming_providers": streaming_providers,
            }
            
        # 2. Fallback to TMDB
        logger.info(f"Movie {tmdb_id} not in local DB. Fetching from TMDB.")
        details = await tmdb.get_movie_details(tmdb_id)
        if not details:
            raise HTTPException(status_code=404, detail="Movie not found in external provider.")
            
        return {
            "tmdb_id": tmdb_id,
            "title": details.get("title"),
            "year": int(details["release_date"][:4]) if details.get("release_date") else None,
            "runtime": details.get("runtime"),
            "genres": [g["name"] for g in details.get("genres", [])],
            "overview": details.get("overview", ""),
            "poster_url": details.get("poster_path"),
            "match_score": 0,
            "vectorbox_score": None
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch movie details for {tmdb_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/{tmdb_id}/rate")
@limiter.limit("60/minute")
async def rate_movie(
    # slowapi needs the starlette Request named `request` (see rss.py group/vibe).
    request: Request,
    tmdb_id: int,
    payload: RateMovieRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user),
    tmdb: TMDBClient = Depends(get_tmdb_client)
):
    """
    Rate a movie, add to watchlist, or like it.
    Updates the 'profile_dirty' flag in Redis for LLM profile summary regeneration.
    """
    try:
        # 1. Ensure movie exists in local DB.
        # Use MovieService.get_or_create_movie so the new row goes through the
        # canonical MovieFactory path (TMDB + OMDb + Qdrant). This populates
        # imdb_id, imdb_rating, metacritic_rating, vectorbox_score, keywords,
        # directors, cast, tagline, mpaa_rating, awards, oscar_wins, etc. —
        # otherwise Phase 1 of maintenance_orchestrator would skip the row
        # forever (it filters by imdb_id IS NOT NULL).
        movie_service = MovieService(db, tmdb=tmdb)
        movie = await movie_service.get_or_create_movie(tmdb_id)
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found in TMDB.")
        
        # 2. Upsert UserRating
        # Note: Index elements must match the unique constraint (idx_user_movie)
        stmt = insert(UserRating).values(
            user_id=current_user.user_id,
            movie_id=movie.id,
            rating=payload.rating,
            is_watchlist=payload.is_watchlist,
            is_liked=payload.is_liked,
            is_watched=True if payload.rating is not None else False,
            watched_date=datetime.now(timezone.utc) if payload.rating is not None else None,
            created_at=datetime.now(timezone.utc)
        ).on_conflict_do_update(
            index_elements=['user_id', 'movie_id'],
            set_={
                # Preserve an existing rating when the caller only toggles
                # watchlist/like (rating=None) — e.g. dossier "+ watchlist".
                'rating': payload.rating if payload.rating is not None else UserRating.rating,
                'is_watchlist': payload.is_watchlist,
                'is_liked': payload.is_liked,
                'is_watched': True if payload.rating is not None else UserRating.is_watched,
                'watched_date': datetime.now(timezone.utc) if payload.rating is not None else UserRating.watched_date,
            }
        )
        
        await db.execute(stmt)
        await db.commit()

        # F-37: keep onboarding_completed in sync. The carousel endpoint sets
        # this flag at ≥15 ratings, but this route used to skip it — users
        # who built up their library via /movies/{id}/rate stayed flagged as
        # in-onboarding forever.
        await maybe_complete_onboarding(current_user.user_id, db)

        # 3. Mark profile as dirty in Redis
        await set_profile_dirty(current_user.user_id, REDIS_URL)

        return {"status": "success", "message": "Rating updated and profile marked dirty."}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to rate movie {tmdb_id}: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update rating")
