"""
Additional tools: Group watchlist, compatibility test
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List
import numpy as np
import logging

from config import get_db
from dependencies import get_qdrant_service, get_current_user
from models.database import UserRating, Movie, User
from models.schemas import GroupWatchlistRequest, CompatibilityRequest, CompatibilityResponse, TokenResponse
from services.qdrant_service import QdrantService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/group-watchlist")
async def create_group_watchlist(
    request: GroupWatchlistRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user)
):
    if current_user.user_id not in request.user_ids:
        raise HTTPException(status_code=403, detail="Access denied")
    """
    Find intersection of multiple users' watchlists
    Rank by average rating
    """
    if len(request.user_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 users required")
    
    # Get all movies rated by ALL users
    movie_ratings = {}
    
    for user_id in request.user_ids:
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
        )
        user_movies = result.all()
        
        for rating, movie in user_movies:
            if movie.id not in movie_ratings:
                movie_ratings[movie.id] = {
                    "movie": movie,
                    "ratings": [],
                    "users": set()
                }
            movie_ratings[movie.id]["ratings"].append(rating.rating)
            movie_ratings[movie.id]["users"].add(user_id)
    
    # Filter to movies watched by ALL users
    intersection = [
        {
            "movie": data["movie"],
            "avg_rating": np.mean(data["ratings"]),
            "ratings": data["ratings"]
        }
        for movie_id, data in movie_ratings.items()
        if len(data["users"]) == len(request.user_ids)
    ]
    
    # Apply minimum rating filter
    if request.min_avg_rating:
        intersection = [
            m for m in intersection
            if m["avg_rating"] >= request.min_avg_rating
        ]
    
    # Sort by average rating
    intersection.sort(key=lambda x: x["avg_rating"], reverse=True)
    
    return {
        "total_movies": len(intersection),
        "movies": intersection[:50]  # Limit results
    }


@router.post("/compatibility", response_model=CompatibilityResponse)
async def calculate_compatibility(
    request: CompatibilityRequest,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    qdrant: QdrantService = Depends(get_qdrant_service)
):
    """
    Calculate compatibility between two users
    Using cosine similarity of their taste vectors
    """
    if current_user.user_id not in [request.user_id_1, request.user_id_2]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get both users
    result = await db.execute(
        select(User).where(User.id.in_([request.user_id_1, request.user_id_2]))
    )
    users = {u.id: u for u in result.scalars().all()}

    if len(users) != 2:
        raise HTTPException(status_code=404, detail="One or both users not found")

    # Get rated movies for both users
    user1_ratings = await db.execute(
        select(UserRating, Movie)
        .join(Movie, UserRating.movie_id == Movie.id)
        .where(UserRating.user_id == request.user_id_1)
    )
    user1_movies = user1_ratings.all()

    user2_ratings = await db.execute(
        select(UserRating, Movie)
        .join(Movie, UserRating.movie_id == Movie.id)
        .where(UserRating.user_id == request.user_id_2)
    )
    user2_movies = user2_ratings.all()

    # Find shared movies (by internal id, which is correct here — intersection count only)
    user1_movie_ids = {movie.id for _, movie in user1_movies}
    user2_movie_ids = {movie.id for _, movie in user2_movies}
    shared_movie_ids = user1_movie_ids & user2_movie_ids

    # Calculate genre overlap
    user1_genres = set()
    user2_genres = set()
    for _, movie in user1_movies:
        if movie.genres:
            user1_genres.update(movie.genres)
    for _, movie in user2_movies:
        if movie.genres:
            user2_genres.update(movie.genres)

    shared_genres = list(user1_genres & user2_genres)

    # Batch-fetch all vectors (Qdrant is indexed by tmdb_id)
    all_tmdb_ids = [
        movie.tmdb_id for _, movie in user1_movies + user2_movies
        if movie.tmdb_id is not None
    ]
    vectors_map = await qdrant.get_vectors_batch(all_tmdb_ids)

    # Compute average taste vectors using batch results
    user1_vectors = []
    user2_vectors = []

    for rating, movie in user1_movies:
        vector = vectors_map.get(movie.tmdb_id)
        if vector and rating.rating:
            weighted_vector = np.array(vector) * (rating.rating / 5.0)
            user1_vectors.append(weighted_vector)

    for rating, movie in user2_movies:
        vector = vectors_map.get(movie.tmdb_id)
        if vector and rating.rating:
            weighted_vector = np.array(vector) * (rating.rating / 5.0)
            user2_vectors.append(weighted_vector)

    if not user1_vectors or not user2_vectors:
        raise HTTPException(
            status_code=400,
            detail="Insufficient data for compatibility calculation"
        )

    # Average vectors
    user1_avg = np.mean(user1_vectors, axis=0)
    user2_avg = np.mean(user2_vectors, axis=0)

    # Cosine similarity
    similarity = np.dot(user1_avg, user2_avg) / (
        np.linalg.norm(user1_avg) * np.linalg.norm(user2_avg)
    )

    return CompatibilityResponse(
        user_1=users[request.user_id_1].username,
        user_2=users[request.user_id_2].username,
        similarity_score=float(similarity),
        shared_movies=len(shared_movie_ids),
        shared_genres=shared_genres[:10]
    )


from fastapi import BackgroundTasks
from config import AsyncSessionLocal

async def run_popular_update_task():
    """Background task to run the update"""
    try:
        from scripts.popular_scraper import scrape_letterboxd_popular
        await scrape_letterboxd_popular()
        logger.info("Background popular update finished")
    except Exception as e:
        logger.error(f"Background update failed: {e}")

@router.post("/update-popular")
async def update_popular_movies(
    background_tasks: BackgroundTasks,
    current_user: TokenResponse = Depends(get_current_user)
):
    """
    Manually trigger the 'Popular on Letterboxd' scraper in the background.
    """
    background_tasks.add_task(run_popular_update_task)
    return {"status": "success", "message": "Popular movies update started in background"}
