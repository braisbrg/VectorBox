import asyncio
import httpx
from bs4 import BeautifulSoup
import logging
from typing import List, Optional, Dict
import re

logger = logging.getLogger(__name__)

class ScraperService:
    def __init__(self):
        self.headers = {
            "User-Agent": "VectorBox-Student-Project/1.0 (Educational Purpose)"
        }
        # No self.client — cada método crea su propio AsyncClient
        # (correcto para uso en contexto async, evita estado compartido)

    async def close(self) -> None:
        """No-op — ScraperService creates HTTP sessions per-request."""
        pass

    async def _scrape_watchlist_page(self, username: str, page: int) -> tuple[list[dict], bool]:
        """Scrape one page of the user's watchlist.

        Returns (films, has_more). has_more=False signals the caller to stop
        (no posters found on this page, or last page reached).
        """
        url = (
            f"https://letterboxd.com/{username}/watchlist/"
            if page == 1
            else f"https://letterboxd.com/{username}/watchlist/page/{page}/"
        )
        logger.info(f"Scraping watchlist page {page} for {username}: {url}")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.headers, follow_redirects=True)
                if response.status_code == 404:
                    # Past the last page — Letterboxd returns 404 for page N+1.
                    return [], False
                response.raise_for_status()
                html = response.text
        except Exception as e:
            logger.error(f"Error scraping watchlist page {page}: {e}")
            return [], False

        soup = BeautifulSoup(html, "html.parser")
        poster_containers = soup.find_all("div", attrs={"data-component-class": "LazyPoster"})
        if not poster_containers:
            poster_containers = soup.find_all("li", class_="poster-container")

        films = self._parse_poster_containers(poster_containers)
        # No posters found → empty page → end pagination.
        has_more = bool(films)
        return films, has_more

    async def scrape_watchlist_all(self, username: str, max_pages: int = 50) -> List[dict]:
        """Scrape every page of a user's watchlist.

        Paginates `/watchlist/`, `/watchlist/page/2/`, etc. until an empty page
        or `max_pages` (50 = ~1400 films, generous). 1-second rate-limit between
        pages to be polite to Letterboxd. Returns merged list of unique films
        (film_slug deduplicated).
        """
        all_films: list[dict] = []
        seen_slugs: set[str] = set()
        for page in range(1, max_pages + 1):
            films, has_more = await self._scrape_watchlist_page(username, page)
            for f in films:
                slug = f.get("film_slug")
                if slug and slug not in seen_slugs:
                    seen_slugs.add(slug)
                    all_films.append(f)
            if not has_more:
                break
            if page < max_pages:
                # Rate-limit between page fetches. Letterboxd doesn't publish a
                # rate limit but 1s is conservative and adds at most
                # ~max_pages seconds to a full sync (~50s worst case).
                await asyncio.sleep(1.0)
        logger.info(f"Watchlist scrape complete for {username}: {len(all_films)} films across {page} pages")
        return all_films

    async def scrape_watchlist_recent(self, username: str) -> List[dict]:
        """Legacy first-page-only scrape, preserved for callers that don't
        want the full pagination cost. New callers should prefer
        `scrape_watchlist_all`."""
        films, _ = await self._scrape_watchlist_page(username, page=1)
        return films

    def _parse_poster_containers(self, poster_containers) -> list[dict]:
        """Extract {film_slug, year, title} from a list of poster containers."""
        films: list[dict] = []
        for container in poster_containers:
            # New React structure
            if container.name == "div":
                slug_raw = container.get("data-item-slug")
                # Security: strict slug validation
                if slug_raw and re.match(r"^[a-zA-Z0-9-]+$", slug_raw):
                    film_slug = slug_raw
                else:
                    film_slug = None

                film_name = container.get("data-item-name")  # "Title (Year)"

                # Parse "Title (Year)" → (title, year). data-item-name preserves
                # accents and punctuation that the slug strips — use it as the
                # TMDB search query in the fuzzy fallback.
                year = None
                title = None
                if film_name:
                    m = re.match(r"^(.*?)\s*\((\d{4})\)\s*$", film_name)
                    if m:
                        title = m.group(1).strip() or None
                        try:
                            year = int(m.group(2))
                        except ValueError:
                            pass
                    else:
                        title = film_name.strip() or None

                if film_slug:
                    films.append({
                        "film_slug": film_slug,
                        "year": year,
                        "title": title,
                    })

            # Old structure (li.poster-container)
            else:
                div_poster = container.find("div", class_="film-poster")
                if div_poster:
                    film_slug = div_poster.get("data-film-slug")
                    film_year = div_poster.get("data-film-release-year")
                    # Old layout doesn't expose title cleanly; leave None and
                    # the fuzzy gate will fall back to refusing the resolution.
                    if film_slug:
                        films.append({
                            "film_slug": film_slug,
                            "year": int(film_year) if film_year else None,
                            "title": None,
                        })

        return films

    async def _fetch_film_page(self, url: str) -> Optional[str]:
        """Fetch a Letterboxd film page HTML. Uses curl_cffi with Chrome
        fingerprint impersonation first (Letterboxd / Cloudflare frequently
        block plain httpx on individual film pages but not on the watchlist
        listing). Falls back to httpx on transient curl_cffi errors.

        One retry with 1s backoff on 5xx / connection errors before giving up.
        Returns HTML or None.
        """
        # Try curl_cffi (Chrome 124 impersonation) first.
        try:
            from curl_cffi import requests as curl_requests
            async with curl_requests.AsyncSession() as session:
                for attempt in range(2):
                    try:
                        resp = await session.get(
                            url, headers=self.headers, impersonate="chrome124", timeout=10
                        )
                        if resp.status_code == 200:
                            return resp.text
                        if resp.status_code >= 500 and attempt == 0:
                            await asyncio.sleep(1.0)
                            continue
                        if resp.status_code == 404:
                            return None
                        logger.warning(f"[scraper] curl_cffi {url} -> {resp.status_code}")
                        break
                    except Exception as e:
                        if attempt == 0:
                            logger.info(f"[scraper] curl_cffi attempt 1 failed for {url}: {e}; retrying once")
                            await asyncio.sleep(1.0)
                            continue
                        logger.warning(f"[scraper] curl_cffi exhausted for {url}: {e}; falling back to httpx")
                        break
        except ImportError:
            # curl_cffi not installed — go straight to httpx.
            pass

        # Fallback: plain httpx with one retry.
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for attempt in range(2):
                    try:
                        response = await client.get(url, headers=self.headers)
                        if response.status_code == 200:
                            return response.text
                        if response.status_code >= 500 and attempt == 0:
                            await asyncio.sleep(1.0)
                            continue
                        logger.warning(f"[scraper] httpx {url} -> {response.status_code}")
                        return None
                    except (httpx.TimeoutException, httpx.ConnectError) as e:
                        if attempt == 0:
                            await asyncio.sleep(1.0)
                            continue
                        logger.warning(f"[scraper] httpx exhausted for {url}: {e}")
                        return None
        except Exception as e:
            logger.error(f"[scraper] page fetch failed for {url}: {e}")
        return None

    async def get_tmdb_id(self, film_slug: str) -> Optional[int]:
        """
        Visits the Letterboxd film page to extract the authoritative TMDB ID.
        This avoids ambiguity with Title+Year matching.
        """
        url = f"https://letterboxd.com/film/{film_slug}/"
        html = await self._fetch_film_page(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")

        # Letterboxd usually puts the TMDB ID in the body tag: data-tmdb-id="..."
        body = soup.find("body")
        if body and body.has_attr("data-tmdb-id"):
            try:
                tmdb_id = int(body["data-tmdb-id"])
                logger.info(f"Found TMDB ID {tmdb_id} for {film_slug}")
                return tmdb_id
            except ValueError:
                pass

        # Fallback: Look for links to TMDB
        tmdb_link = soup.find("a", href=lambda href: href and "themoviedb.org/movie/" in href)
        if tmdb_link:
            try:
                href = tmdb_link["href"]
                parts = href.split("themoviedb.org/movie/")
                if len(parts) > 1:
                    id_part = parts[1].split("/")[0].split("?")[0]
                    tmdb_id = int(id_part)
                    logger.info(f"Found TMDB ID {tmdb_id} from link for {film_slug}")
                    return tmdb_id
            except (ValueError, IndexError, KeyError):
                pass

        logger.warning(f"Could not find TMDB ID for {film_slug}")
        return None

    async def scrape_popular_this_week(self) -> List[Dict]:
        """
        Scrapes the 'Popular This Week' list from Letterboxd.
        Returns a list of dicts with title, year, letterboxd_slug, letterboxd_rating.
        """
        url = "https://letterboxd.com/films/ajax/popular/this/week/?esiAllowFilters=true"
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.headers)
                
            if response.status_code != 200:
                logger.error(f"Failed to fetch popular movies: {response.status_code}")
                return []

            soup = BeautifulSoup(response.content, 'html.parser')
            
            # The structure is a list of li.posteritem
            items = soup.select("li.posteritem")
            
            popular_movies = []
            
            for item in items:
                try:
                    # Rating is on the li element
                    rating_str = item.get('data-average-rating')
                    rating = float(rating_str) if rating_str else 0.0
                    
                    # Movie details are in the child div
                    div = item.select_one("div.react-component")
                    if not div:
                        continue
                        
                    slug = div.get('data-item-slug')
                    name_year = div.get('data-item-name')  # e.g. "Wicked (2024)"
                    
                    if not slug or not name_year:
                        continue

                    # Parse title and year from "Title (Year)"
                    match = re.match(r"(.*)\s\((\d{4})\)$", name_year)
                    if match:
                        title = match.group(1)
                        year = int(match.group(2))
                    else:
                        title = name_year
                        year = None

                    popular_movies.append({
                        "title": title,
                        "year": year,
                        "letterboxd_slug": slug,
                        "letterboxd_rating": rating
                    })
                    
                except Exception as e:
                    logger.warning(f"Error parsing popular movie item: {e}")
                    continue
            
            logger.info(f"Found {len(popular_movies)} popular films")
            return popular_movies

        except Exception as e:
            logger.error(f"Error scraping popular movies: {e}")
            return []