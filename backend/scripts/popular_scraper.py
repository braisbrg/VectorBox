import asyncio
import os
import sys
import logging
import httpx
from bs4 import BeautifulSoup
from typing import List
import redis.asyncio as redis
import json
import re

# Fix paths
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.tmdb_client import TMDBClient
from config import AsyncSessionLocal
from models.database import Movie
from sqlalchemy import select

from services.movie_service import MovieService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REDIS_KEY_POPULAR = "cache:feed:popular_letterboxd:ids"
# AJAX endpoint returns pre-rendered grid (bypassing full page skeleton)
LETTERBOXD_URL = "https://letterboxd.com/films/ajax/popular/this/week/?esiAllowFilters=true"

async def scrape_letterboxd_popular():
    """
    Scrapes Letterboxd Popular films via AJAX endpoint.
    Matches slugs to TMDB IDs.
    Extracts 'data-average-rating'.
    Updates 'Movie' in DB with rating.
    Stores list of IDs in Redis.
    """
    logger.info(f"Scraping {LETTERBOXD_URL}...")
    
    tmdb = TMDBClient()
    
    # 1. Fetch Page
    # Mimic real browser to avoid soft-blocks (Cloudflare)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        # "X-Requested-With": "XMLHttpRequest", # Sometimes helps, sometimes hurts. Keeping it simple first.
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }
    
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(LETTERBOXD_URL, headers=headers)
            if response.status_code != 200:
                logger.error(f"Failed to fetch page. Status: {response.status_code}")
                return
            html = response.text
    except Exception as e:
        logger.error(f"Network error: {e}")
        return

    # 2. Parse HTML
    try:
        soup = BeautifulSoup(html, "html.parser")
        
        # New Structure: <li class="posteritem" data-average-rating="4.30">
        items = soup.select("li.posteritem")
        
        movie_data = [] # List of dicts
        
        for item in items:
            # Extract Rating
            rating_str = item.get("data-average-rating")
            rating = float(rating_str) if rating_str else None
            
            # Find inner div with slug/name
            # Look for div with data-item-slug OR data-film-slug
            comp = item.select_one("div[data-item-slug]") 
            if not comp:
                 comp = item.select_one("div[data-film-slug]")
            
            if comp:
                slug = comp.get("data-item-slug") or comp.get("data-film-slug")
                name_attr = comp.get("data-item-name") # e.g. "Wake Up Dead Man (2025)"
                
                entry = {"slug": slug, "rating": rating}
                
                if name_attr:
                    # Regex to extract Title and Year
                    match = re.match(r"^(.*?) \((\d{4})\)$", name_attr)
                    if match:
                        entry["title"] = match.group(1)
                        entry["year"] = int(match.group(2))
                    else:
                         entry["title"] = name_attr
                         entry["year"] = None
                else:
                    entry["title"] = None
                    entry["year"] = None
                    
                if slug:
                    movie_data.append(entry)

        # Debug check
        if not movie_data:
            logger.warning("⚠️ No movies found (0 items).")
            return

        logger.info(f"Found {len(movie_data)} movies to resolve...")

    except Exception as e:
        logger.error(f"Parsing error: {e}")
        return

    # 3. Resolve to TMDB IDs & Update DB
    tmdb_ids = []
    
    logger.info("Resolving movies to TMDB IDs and Updating DB...")
    
    async with AsyncSessionLocal() as db:
        # Initialize MovieService with the session
        movie_service = MovieService(db)
        
        try:
            for item in movie_data:
                try:
                    result_movie = None
                    
                    # Strategy A: Precise Search (Title + Year)
                    if item["title"]:
                        result_movie = await tmdb.search_movie(item["title"], year=item["year"])
                    
                    # Strategy B: Fallback to Slug
                    if not result_movie and item["slug"]:
                        query = item["slug"].replace("-", " ")
                        result_movie = await tmdb.search_movie(query)
                    
                    if result_movie:
                        tmdb_id = result_movie["id"]
                        tmdb_ids.append(tmdb_id)
                        
                        # Construct URI
                        uri = f"https://letterboxd.com/film/{item['slug']}/"
                        item["uri"] = uri
                        
                        # --- DB UPSERT via MovieService (Async & Enriched) ---
                        # This will:
                        # 1. Check if movie exists
                        # 2. If not, fetch full TMDB details + OMDb data + Calculate VB Score
                        # 3. Generate Qdrant Vector
                        # 4. Save everything
                        db_movie = await movie_service.get_or_create_movie(tmdb_id, letterboxd_uri=uri)
                        
                        if db_movie:
                            # Update Rating if available
                            if item["rating"] is not None:
                                 db_movie.letterboxd_rating = item["rating"]
                                 # We need to commit the rating change
                                 await db.commit()
                            
                            # Heal missing VectorBox Score (for legacy movies)
                            if db_movie.vectorbox_score is None or db_movie.vectorbox_score == 0:
                                logger.info(f"Missing/Zero VB Score for {db_movie.title}. Attempting enrichment...")
                                await movie_service.enrich_movie(db_movie)
                        else:
                            logger.error(f"Failed to ingest movie: {result_movie['title']}")

                    else:
                        logger.warning(f"Could not resolve: {item.get('title') or item.get('slug')}")
                        
                except Exception as e:
                    logger.error(f"Error processing item {item.get('title')}: {e}")
                    # Rollback handled by get_or_create_movie for ingestion, but for outer loop safety:
                    await db.rollback()
        
        except Exception as e:
            logger.error(f"Global DB loop error: {e}")
        
        finally:
            # Clean up services
            await movie_service.close()
            
    logger.info(f"Resolved {len(tmdb_ids)}/{len(movie_data)} movies.")

    # 4. Store in Redis
    if tmdb_ids:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        try:
            r = await redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            
            # Store as JSON string
            await r.set(REDIS_KEY_POPULAR, json.dumps(tmdb_ids), ex=60*60*24) # 24h expire
            logger.info("Saved Popular IDs to Redis.")
            
            await r.close()
        except Exception as e:
            logger.error(f"Redis error: {e}")
            
    await tmdb.close()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    asyncio.run(scrape_letterboxd_popular())
