from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import List, Dict
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import User, Movie, UserRating
from services.movie_service import MovieService
from services.rss_service import RSSService
from services.scraper_service import ScraperService
from services.tmdb_client import TMDBClient
from config import get_db
import logging

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["rss"],
    responses={404: {"description": "Not found"}},
)

class SyncResponse(BaseModel):
    status: str
    stats: Dict[str, int]
    message: str

class GroupVibeRequest(BaseModel):
    usernames: List[str]

@router.post("/sync/{username}", response_model=SyncResponse)
async def sync_user_data(
    username: str, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Syncs user data from Letterboxd:
    1. RSS Feed (Diary/Reviews) - For watched history and ratings.
    2. Watchlist Scraping (Light) - For recent watchlist additions.
    
    Note: username is the VectorBox username. Data is fetched from the linked letterboxd_username.
    """
    try:
        # Verify user exists
        logger.info(f"Sync requested for user: {username}")
        
        stmt = select(User).where(User.username == username)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            # Auto-create user if not exists (for convenience)
            user = User(username=username, letterboxd_username=username)  # Default: same as VectorBox username
            db.add(user)
            await db.flush()
        
        # Determine which Letterboxd profile to use for data fetching
        letterboxd_profile = user.letterboxd_username or username
        logger.info(f"Using Letterboxd profile '{letterboxd_profile}' for user '{username}'")
        
        # 1. RSS Sync (Uses letterboxd_username)
        rss_service = RSSService(db)
        rss_result = await rss_service.sync_user_rss(letterboxd_profile, user.id)
        
        # 2. Watchlist Sync (Uses letterboxd_username)
        scraper = ScraperService()
        import asyncio
        loop = asyncio.get_running_loop()
        watchlist_items = await loop.run_in_executor(None, scraper.scrape_watchlist_recent, letterboxd_profile)
        
        tmdb = TMDBClient()
        movie_service = MovieService(db)
        
        watchlist_added = 0
        
        for item in watchlist_items:
            film_slug = item["film_slug"]
            film_year = item.get("year")
            search_query = film_slug.replace("-", " ")
            
            # 1. Try to get authoritative TMDB ID from Letterboxd page
            # Run in executor because it uses requests.get (blocking)
            tmdb_id = await loop.run_in_executor(None, scraper.get_tmdb_id, film_slug)
            
            if tmdb_id:
                logger.info(f"Found authoritative TMDB ID {tmdb_id} for {film_slug}")
            else:
                # 2. Fallback to Search TMDB (Fuzzy)
                logger.info(f"No ID found on page for {film_slug}. Fallback to search...")
                params = {"query": search_query}
                if film_year:
                    params["year"] = film_year
                
                tmdb_results = await tmdb._make_request("/search/movie", params)
                
                if tmdb_results and tmdb_results.get("results"):
                    top_match = tmdb_results["results"][0]
                    tmdb_id = top_match["id"]
                    logger.info(f"Found fuzzy match: {top_match['title']} (ID: {tmdb_id})")
            
            if tmdb_id:
                # Use MovieService to get or create (with full enrichment)
                movie = await movie_service.get_or_create_movie(
                    tmdb_id=tmdb_id, 
                    letterboxd_uri=f"https://letterboxd.com/film/{film_slug}/"
                )

                # Strict Year Check (Only if we did a fuzzy search)
                # If we got the ID from Letterboxd directly, we trust it 100%
                scraped_id = await loop.run_in_executor(None, scraper.get_tmdb_id, film_slug)
                if not scraped_id and film_year and movie.year and abs(movie.year - int(film_year)) > 1:
                    logger.warning(f"Year mismatch for {film_slug}: Scraped {film_year} vs DB {movie.year}. Skipping.")
                    continue

                # Add to User Watchlist (Using UserRating table)
                if movie:
                    # Check if rating entry exists
                    rating_stmt = select(UserRating).where(
                        UserRating.user_id == user.id,
                        UserRating.movie_id == movie.id
                    )
                    existing_rating = await db.execute(rating_stmt)
                    rating_entry = existing_rating.scalars().first()
                    
                    if rating_entry:
                        # Update existing entry if not already in watchlist
                        if not rating_entry.is_watchlist:
                            rating_entry.is_watchlist = True
                            watchlist_added += 1
                            logger.info(f"Updated existing rating to watchlist: {movie.title}")
                        else:
                            logger.info(f"Already in watchlist: {movie.title}")
                    else:
                        # Create new entry
                        new_rating = UserRating(
                            user_id=user.id,
                            movie_id=movie.id,
                            is_watchlist=True
                        )
                        db.add(new_rating)
                        watchlist_added += 1
                        logger.info(f"Added new watchlist item: {movie.title}")
        
        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(f"DB commit failed during sync_user_data: {e}")
            raise
        await tmdb.close()
        
        return {
            "status": "success",
            "stats": {
                "rss_new_movies": rss_result.get("new_movies", 0),
                "rss_new_ratings": rss_result.get("new_ratings", 0),
                "rss_updated_ratings": rss_result.get("updated_ratings", 0),
                "rss_errors": rss_result.get("errors", 0),
                "watchlist_added": watchlist_added
            },
            "message": f"Synced. Watchlist added: {watchlist_added}"
        }

    except Exception as e:
        logger.error(f"Sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/group/vibe")
async def get_group_recommendations(
    request: GroupVibeRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Get recommendations based on the 'Group Vibe' (centroid of multiple users).
    """
    rss_service = RSSService(db)
    
    # Get Hybrid Recommendations
    scored_results = await rss_service.get_group_recommendations_hybrid(request.usernames)
    
    if not scored_results:
        return []
        
    # Fetch full movie details
    tmdb_ids = [res['tmdb_id'] for res in scored_results]
    
    stmt = select(Movie).where(Movie.tmdb_id.in_(tmdb_ids))
    result = await db.execute(stmt)
    db_movies = result.scalars().all()
    
    # Map back to results to keep order/score
    movie_map = {m.tmdb_id: m for m in db_movies}
    
    final_results = []
    
    for res in scored_results:
        if len(final_results) >= 20:
            break
            
        tmdb_id = res['tmdb_id']
        movie = movie_map.get(tmdb_id)
        
        # If missing in DB, try to ingest
        if not movie:
            try:
                # Fetch details
                details = await rss_service.tmdb.get_movie_details(tmdb_id)
                
                if not details:
                    logger.warning(f"TMDB ID {tmdb_id} not found (404). Deleting from Qdrant...")
                    await rss_service.qdrant.delete_movie(tmdb_id)
                    continue
                    
                # Create Movie object
                new_movie = Movie(
                    tmdb_id=tmdb_id,
                    title=details.get('title'),
                    original_title=details.get('original_title'),
                    year=int(details.get('release_date', '0000')[:4]) if details.get('release_date') else None,
                    runtime=details.get('runtime'),
                    genres=[g['name'] for g in details.get('genres', [])],
                    overview=details.get('overview'),
                    poster_path=details.get('poster_path'),
                    backdrop_path=details.get('backdrop_path'),
                    vote_average=details.get('vote_average'),
                    vote_count=details.get('vote_count'),
                    popularity=details.get('popularity'),
                    original_language=details.get('original_language')
                )
                db.add(new_movie)
                try:
                    await db.commit()
                    await db.refresh(new_movie)
                except Exception as e:
                    await db.rollback()
                    logger.error(f"DB commit failed during missing movie ingestion: {e}")
                    raise
                movie = new_movie
                logger.info(f"Ingested missing movie: {new_movie.title}")
                
            except Exception as e:
                logger.error(f"Failed to ingest/clean movie {tmdb_id}: {e}")
                continue

        if movie:
            # Fetch streaming providers (Default to ES for now)
            providers_data = await rss_service.tmdb.get_watch_providers(movie.tmdb_id, "ES")
            flat_providers = []
            if providers_data and 'flatrate' in providers_data:
                 flat_providers = [p['provider_name'] for p in providers_data['flatrate']]
            
            final_results.append({
                "movie": movie,
                "similarity_score": res['score'],
                "providers": flat_providers,
                "contributors": [
                    {"seed_title": c["username"], "contribution": c["score"]} 
                    for c in res.get("contributors", [])
                ]
            })
            
    return final_results
