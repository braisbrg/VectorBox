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

                # Step A: parallelize TMDB searches with a concurrency cap
                sem = asyncio.Semaphore(5)

                async def _resolve(idx: int, slug: str):
                    async with sem:
                        try:
                            query = slug.replace("-", " ")
                            year = None
                            query = re.sub(r'\s\d$', '', query)
                            year_match = re.search(r'\s(\d{4})$', query)
                            if year_match:
                                potential_year = int(year_match.group(1))
                                if 1888 <= potential_year <= 2030:
                                    year = potential_year
                                    query = query[:year_match.start()].strip()

                            result_movie = await tmdb.search_movie(query, year=year)
                            if not result_movie and year:
                                result_movie = await tmdb.search_movie(query)
                            return idx, slug, result_movie
                        except Exception as e:
                            logger.warning(f"Search failed for {slug}: {e}")
                            return idx, slug, None

                try:
                    search_results = await asyncio.gather(
                        *[_resolve(i, s) for i, s in enumerate(unique_slugs)]
                    )

                    # Step B: sequential ingest (DB session is single-threaded);
                    # single commit at the end of the batch.
                    for idx, slug, result_movie in search_results:
                        if not result_movie:
                            logger.warning(f"Could not resolve slug: {slug}")
                            failed_slugs.append(slug)
                            continue
                        try:
                            tmdb_id = result_movie["id"]
                            tmdb_ids.append(tmdb_id)
                            uri = f"https://letterboxd.com/film/{slug}/"
                            db_movie = await movie_service.get_or_create_movie(tmdb_id, letterboxd_uri=uri)
                            if idx < len(ratings) and db_movie:
                                try:
                                    db_movie.letterboxd_rating = float(ratings[idx])
                                except (ValueError, TypeError) as e:
                                    logger.warning(f"Failed to parse rating for slug {slug}: {e}")
                        except Exception as inner_e:
                            logger.warning(f"Skipping {slug}: {inner_e}")
                            failed_slugs.append(f"{slug} (Error)")

                    try:
                        await db.commit()
                    except Exception as e:
                        await db.rollback()
                        logger.error(f"Final batch commit failed: {e}")
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
                    r = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
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