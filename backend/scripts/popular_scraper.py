import asyncio
import os
import sys
import logging
import re
import json
from curl_cffi.requests import AsyncSession
import redis.asyncio as redis

# Fix paths
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.tmdb_client import TMDBClient
from config import AsyncSessionLocal, FEED_CACHE_VERSION
from services.movie_service import MovieService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REDIS_KEY_POPULAR = f"cache:{FEED_CACHE_VERSION}:popular_letterboxd:ids"

# TRUCO: Usamos directamente la URL AJAX que contiene los datos crudos
# Esta URL devuelve HTML puro con los posters, no una app React vacía.
LETTERBOXD_AJAX_URL = "https://letterboxd.com/films/ajax/popular/this/week/?esiAllowFilters=true"

async def scrape_letterboxd_popular():
    """
    Scrapes Letterboxd Popular films via curl_cffi + Regex.
    Targets the internal AJAX endpoint to ensure data presence.
    """
    logger.info(f"Scraping {LETTERBOXD_AJAX_URL} with curl_cffi...")
    
    tmdb = TMDBClient()
    
    # Headers quirúrgicos para parecer una navegación interna
    headers = {
        "Referer": "https://letterboxd.com/films/popular/this/week/",
        "X-Requested-With": "XMLHttpRequest", # Vital para endpoints AJAX
        "Accept": "text/html, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        # Impersonate Chrome para saltar Cloudflare
        async with AsyncSession(impersonate="chrome120") as s:
            # 1. Warm-up (Visita la home para pillar cookies de sesión)
            await s.get("https://letterboxd.com/", headers=headers)
            
            # 2. Petición Real al AJAX
            response = await s.get(LETTERBOXD_AJAX_URL, headers=headers, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch content. Status: {response.status_code}")
                # Si falla aquí, es bloqueo duro de IP.
                return

            html = response.text
            
            # --- REGEX EXTRACTION (Ajustado al HTML de AJAX) ---
            # En el AJAX, suelen usar 'data-film-slug' o 'data-item-slug'
            slugs = re.findall(r'data-film-slug="([^"]+)"', html)
            
            if not slugs:
                slugs = re.findall(r'data-item-slug="([^"]+)"', html)
            
            # Rating regex (data-average-rating="3.45")
            ratings = re.findall(r'data-average-rating="(\d+\.?\d*)"', html)

            # Deduplicación preservando orden
            unique_slugs = [] 
            seen = set()
            for s in slugs:
                if s not in seen:
                    unique_slugs.append(s)
                    seen.add(s)
            
            if not unique_slugs:
                 logger.error("⚠️ 0 Slugs found via Regex. The HTML might be empty or changed.")
                 logger.info(f"HTML Preview: {html[:500]}...") # Ver qué nos devuelve
                 return

            logger.info(f"✅ Extracted {len(unique_slugs)} unique slugs via Regex.")
            
            # 3. Resolve to TMDB IDs & Update DB
            tmdb_ids = []
            failed_slugs = []
            
            async with AsyncSessionLocal() as db:
                movie_service = MovieService(db, tmdb=tmdb)
                
                try:
                    for i, slug in enumerate(unique_slugs):
                        try:
                            # Smart slug parsing for patterns like:
                            # - "sinners-2025" -> title="sinners", year=2025
                            # - "eternity-2025-1" -> title="eternity", year=2025 (strip "-1" disambiguation suffix)
                            query = slug.replace("-", " ")
                            year = None
                            
                            # FIRST: Strip trailing disambiguation suffix (e.g., "1" in "eternity 2025 1")
                            query = re.sub(r'\s\d$', '', query)
                            
                            # THEN: Extract year if at end (e.g., "eternity 2025" -> year=2025)
                            year_match = re.search(r'\s(\d{4})$', query)
                            if year_match:
                                potential_year = int(year_match.group(1))
                                if 1888 <= potential_year <= 2030:
                                    year = potential_year
                                    query = query[:year_match.start()].strip()
                            
                            # Primary search: with year parameter (sorted by popularity)
                            result_movie = await tmdb.search_movie(query, year=year)
                            
                            # Fallback: Try without year if year search failed
                            if not result_movie and year:
                                result_movie = await tmdb.search_movie(query)
                            
                            if result_movie:
                                tmdb_id = result_movie["id"]
                                tmdb_ids.append(tmdb_id)
                                
                                uri = f"https://letterboxd.com/film/{slug}/"
                                
                                # Upsert (crear si no existe)
                                db_movie = await movie_service.get_or_create_movie(tmdb_id, letterboxd_uri=uri)
                                
                                # Actualizar rating si lo tenemos
                                if i < len(ratings) and db_movie:
                                     try:
                                         db_movie.letterboxd_rating = float(ratings[i])
                                     except (ValueError, TypeError) as e:
                                         logger.warning(f"Failed to parse rating for slug {slug}: {e}")
                                
                                # Commit por lotes sería mejor, pero uno a uno es seguro
                                await db.commit()
                            else:
                                logger.warning(f"Could not resolve slug: {slug}")
                                failed_slugs.append(slug)
                        except Exception as inner_e:
                            logger.warning(f"Skipping {slug}: {inner_e}")
                            failed_slugs.append(f"{slug} (Error)")
                            await db.rollback()
                            
                finally:
                    # Clean up batches
                    await movie_service.close()
            
            logger.info(f"🎉 Resolved {len(tmdb_ids)} movies successfully.")
            if failed_slugs:
                logger.warning(f"⚠️ Failed to resolve {len(failed_slugs)} slugs:")
                for fs in failed_slugs:
                    logger.warning(f"   - {fs}")


            # 4. Store in Redis
            if tmdb_ids:
                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
                try:
                    r = await redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
                    await r.set(REDIS_KEY_POPULAR, json.dumps(tmdb_ids), ex=60*60*24)
                    logger.info("Saved Popular IDs to Redis.")
                    await r.close()
                except Exception as e:
                    logger.error(f"Redis error: {e}")

    except Exception as e:
        logger.error(f"Critical Scraper Error: {e}")
    finally:
        await tmdb.aclose()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    asyncio.run(scrape_letterboxd_popular())