from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import List, Dict
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import User, Movie, UserRating
from models.schemas import TokenResponse
from services.movie_service import MovieService
from services.rss_service import RSSService
from services.scraper_service import ScraperService
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.task_store import get_task_store
from config import get_db
from dependencies import get_tmdb_client, get_current_user, get_qdrant_service
import logging
import os

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["rss"],
    responses={404: {"description": "Not found"}},
)

class SyncResponse(BaseModel):
    status: str
    stats: Dict[str, object]
    message: str
    task_id: str | None = None

class GroupVibeRequest(BaseModel):
    usernames: List[str]

async def _invalidate_feed_cache(user_id: int) -> None:
    """Delete all cached feed keys for this user after RSS sync or upload."""
    try:
        import redis.asyncio as aioredis
        import os

        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379")
        r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            from services.feed_service import FEED_CACHE_VERSION
            deleted_count = 0
            # Sweep all key patterns that encode user-specific feed state
            patterns = [
                f"section:{FEED_CACHE_VERSION}:{user_id}:*",
                f"signal_cache:{user_id}:*",
            ]
            for pattern in patterns:
                cursor = 0
                while True:
                    cursor, keys = await r.scan(cursor, match=pattern, count=100)
                    if keys:
                        await r.delete(*keys)
                        deleted_count += len(keys)
                    if cursor == 0:
                        break
            # Delete cluster rotation counter
            await r.delete(f"cluster_rotation:{FEED_CACHE_VERSION}:{user_id}")

            if deleted_count:
                logger.info(f"Invalidated {deleted_count} feed/signal cache keys and rotation for user_id={user_id}")
        finally:
            await r.close()
    except Exception as e:
        logger.error(f"Feed cache invalidation failed for user_id={user_id}: {e}")

async def _run_sync_background(user_id: int, letterboxd_profile: str, tmdb: TMDBClient, task_id: str) -> None:
    """Background task — owns its own session. Never re-raises."""
    from config import AsyncSessionLocal
    task_store = get_task_store()

    # Scout-enrichment client for new movies (watchlist path)
    groq_api_key = os.getenv("GROQ_API_KEY")
    groq_client = None
    if groq_api_key:
        try:
            from openai import AsyncOpenAI
            groq_client = AsyncOpenAI(
                api_key=groq_api_key,
                base_url="https://api.groq.com/openai/v1",
                max_retries=0,
            )
        except ImportError:
            logger.warning("openai package not found; Scout enrichment disabled for RSS watchlist")

    async with AsyncSessionLocal() as db:
        try:
            await task_store.update_progress(task_id, 10, "Syncing Letterboxd ratings...")

            rss_service = RSSService(db, tmdb=tmdb)
            await rss_service.sync_user_rss(letterboxd_profile, user_id)

            await task_store.update_progress(task_id, 40, "Ratings synced. Fetching watchlist...")

            scraper = ScraperService()
            movie_service = MovieService(db, tmdb=tmdb, groq_client=groq_client)
            watchlist_added = 0

            try:
                watchlist_items = await scraper.scrape_watchlist_recent(letterboxd_profile)

                for item in watchlist_items:
                    film_slug = item["film_slug"]
                    film_year = item.get("year")

                    page_tmdb_id = await scraper.get_tmdb_id(film_slug)
                    tmdb_id = page_tmdb_id
                    if tmdb_id:
                        logger.info(f"Found authoritative TMDB ID {tmdb_id} for {film_slug}")
                    else:
                        logger.info(f"No ID found on page for {film_slug}. Fallback to search...")
                        params = {"query": film_slug.replace("-", " ")}
                        if film_year:
                            params["year"] = film_year
                        tmdb_results = await tmdb._make_request("/search/movie", params)
                        if tmdb_results and tmdb_results.get("results"):
                            top_match = tmdb_results["results"][0]
                            tmdb_id = top_match["id"]
                            logger.info(f"Found fuzzy match: {top_match['title']} (ID: {tmdb_id})")

                    if not tmdb_id:
                        continue

                    movie = await movie_service.get_or_create_movie(
                        tmdb_id=tmdb_id,
                        letterboxd_uri=f"https://letterboxd.com/film/{film_slug}/"
                    )

                    if not movie:
                        continue

                    # Year check only for fuzzy matches — reuse page_tmdb_id from first call, no second HTTP request
                    if not page_tmdb_id and film_year and movie.year and abs(movie.year - int(film_year)) > 1:
                        logger.warning(f"Year mismatch for {film_slug}: {film_year} vs {movie.year}. Skipping.")
                        continue

                    rating_stmt = select(UserRating).where(
                        UserRating.user_id == user_id,
                        UserRating.movie_id == movie.id
                    )
                    existing = (await db.execute(rating_stmt)).scalars().first()
                    if existing:
                        if not existing.is_watchlist:
                            existing.is_watchlist = True
                            watchlist_added += 1
                    else:
                        db.add(UserRating(user_id=user_id, movie_id=movie.id, is_watchlist=True))
                        watchlist_added += 1

            finally:
                await scraper.close()
                # tmdb is the injected singleton — never close it

            await db.commit()

            await task_store.update_progress(task_id, 80, "Watchlist synced. Refreshing feed...")

            # Imp 10: Invalidate feed cache after sync completes
            await _invalidate_feed_cache(user_id)

            # Invalidate profile summary cache for LLM regeneration
            from services.profile_cache import invalidate_profile_summary
            from config import REDIS_URL
            await invalidate_profile_summary(user_id, REDIS_URL)

            logger.info(f"Background sync complete for user_id={user_id}. Watchlist added: {watchlist_added}")

            await task_store.complete_task(task_id, "Sync complete")

        except Exception as e:
            logger.error(f"Background sync failed for user_id={user_id}: {e}")
            try:
                await task_store.fail_task(task_id, f"Sync failed: {str(e)}")
            except Exception:
                pass
        finally:
            if groq_client:
                try:
                    await groq_client.close()
                except Exception:
                    pass


@router.post("/sync/{username}", response_model=SyncResponse)
async def sync_user_data(
    username: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: TokenResponse = Depends(get_current_user)
):
    stmt = select(User).where(User.username == username)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Cannot sync another user's account")

    letterboxd_profile = user.letterboxd_username or username

    task_store = get_task_store()
    task_id = task_store.generate_task_id()
    await task_store.create_task(task_id, 100, "Starting Letterboxd sync...", user_id=current_user.user_id)

    background_tasks.add_task(_run_sync_background, user.id, letterboxd_profile, tmdb, task_id)

    return {
        "status": "started",
        "stats": {},
        "message": f"Sync started for {letterboxd_profile}",
        "task_id": task_id,
    }

@router.post("/group/vibe")
async def get_group_recommendations(
    request: GroupVibeRequest,
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    current_user: TokenResponse = Depends(get_current_user)
):
    """
    Get recommendations based on the 'Group Vibe' (centroid of multiple users).
    """
    rss_service = RSSService(db, tmdb=tmdb, qdrant=qdrant)
    
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
        
        # If missing in DB, ingest via MovieService (full enrichment: TMDB + OMDb + embedding + Qdrant)
        if not movie:
            try:
                from services.movie_service import MovieService
                movie_svc = MovieService(db, tmdb=tmdb)
                movie = await movie_svc.get_or_create_movie(tmdb_id)
                if not movie:
                    logger.warning(f"Could not ingest TMDB ID {tmdb_id} for group vibe — skipping")
                    continue
            except Exception as e:
                logger.error(f"Group vibe movie ingest failed for tmdb_id={tmdb_id}: {e}")
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
