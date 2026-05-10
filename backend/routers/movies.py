import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, confloat
from typing import Optional
from limiter import limiter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from datetime import datetime

from config import get_db, REDIS_URL
from models.database import Movie, UserRating
from dependencies import get_tmdb_client, get_current_user
from services.tmdb_client import TMDBClient
from services.profile_cache import set_profile_dirty
from models.schemas import TokenResponse

router = APIRouter()
logger = logging.getLogger(__name__)

class RateMovieRequest(BaseModel):
    rating: Optional[confloat(ge=0, le=5)] = None
    is_watchlist: bool = False
    is_liked: bool = False

@router.get("/{tmdb_id}")
async def get_movie_details(
    tmdb_id: int,
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
            return {
                "tmdb_id": movie.tmdb_id,
                "title": movie.title,
                "year": movie.year,
                "runtime": movie.runtime,
                "genres": movie.genres or [],
                "overview": movie.overview,
                "poster_url": movie.poster_path, # FeedItem expects poster_url
                "match_score": movie.vectorbox_score or 0,
                "vectorbox_score": movie.vectorbox_score,
                "imdb_rating": movie.imdb_rating,
                "metacritic_rating": movie.metacritic_rating,

                "letterboxd_rating": movie.letterboxd_rating,
                "title_es": movie.title_es,
                "overview_es": movie.overview_es
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
    http_request: Request,
    tmdb_id: int,
    request: RateMovieRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user),
    tmdb: TMDBClient = Depends(get_tmdb_client)
):
    """
    Rate a movie, add to watchlist, or like it.
    Updates the 'profile_dirty' flag in Redis for LLM profile summary regeneration.
    """
    try:
        # 1. Ensure movie exists in local DB
        stmt = select(Movie).where(Movie.tmdb_id == tmdb_id)
        result = await db.execute(stmt)
        movie = result.scalars().first()
        
        if not movie:
            # Fetch from TMDB and create if missing
            details = await tmdb.get_movie_details(tmdb_id)
            if not details:
                 raise HTTPException(status_code=404, detail="Movie not found in TMDB.")
            
            movie = Movie(
                tmdb_id=tmdb_id,
                title=details.get("title"),
                year=int(details["release_date"][:4]) if details.get("release_date") else None,
                runtime=details.get("runtime"),
                genres=[g["name"] for g in details.get("genres", [])],
                overview=details.get("overview", ""),
                poster_path=details.get("poster_path"),
                popularity=details.get("popularity", 0),
                vote_average=details.get("vote_average", 0),
                vote_count=details.get("vote_count", 0),
                original_language=details.get("original_language", "en")
            )
            db.add(movie)
            await db.flush() # Get the movie.id
        
        # 2. Upsert UserRating
        # Note: Index elements must match the unique constraint (idx_user_movie)
        stmt = insert(UserRating).values(
            user_id=current_user.user_id,
            movie_id=movie.id,
            rating=request.rating,
            is_watchlist=request.is_watchlist,
            is_liked=request.is_liked,
            is_watched=True if request.rating is not None else False,
            watched_date=datetime.utcnow() if request.rating is not None else None,
            created_at=datetime.utcnow()
        ).on_conflict_do_update(
            index_elements=['user_id', 'movie_id'],
            set_={
                'rating': request.rating,
                'is_watchlist': request.is_watchlist,
                'is_liked': request.is_liked,
                'is_watched': True if request.rating is not None else UserRating.is_watched,
                'watched_date': datetime.utcnow() if request.rating is not None else UserRating.watched_date,
            }
        )
        
        await db.execute(stmt)
        await db.commit()

        # 3. Mark profile as dirty in Redis
        await set_profile_dirty(current_user.user_id, REDIS_URL)

        return {"status": "success", "message": "Rating updated and profile marked dirty."}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to rate movie {tmdb_id}: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update rating")
