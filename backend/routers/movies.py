from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging

from config import get_db
from models.database import Movie
from dependencies import get_tmdb_client
from services.tmdb_client import TMDBClient

router = APIRouter()
logger = logging.getLogger(__name__)

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
                "id": movie.id,
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
                "rotten_tomatoes_rating": movie.rotten_tomatoes_rating,
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
            "id": None,
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
