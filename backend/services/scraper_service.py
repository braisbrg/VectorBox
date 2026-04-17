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

    async def scrape_watchlist_recent(self, username: str) -> List[dict]:
        """
        Scrapes the first page of a user's watchlist to get recent additions.
        Returns a list of dicts with 'film_slug' and 'year' (if available).
        """
        url = f"https://letterboxd.com/{username}/watchlist/"
        logger.info(f"Scraping watchlist for {username}: {url}")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.headers, follow_redirects=True)
                response.raise_for_status()
                html = response.text
                    
            soup = BeautifulSoup(html, 'html.parser')
            
            # Find the grid of posters (React components)
            poster_containers = soup.find_all("div", attrs={"data-component-class": "LazyPoster"})
            
            # Fallback to old selector if new one fails
            if not poster_containers:
                poster_containers = soup.find_all("li", class_="poster-container")

            logger.info(f"Found {len(poster_containers)} poster containers")

            films = []
            for container in poster_containers:
                # New React structure
                if container.name == "div":
                    slug_raw = container.get("data-item-slug")
                    # Security: strict slug validation
                    if slug_raw and re.match(r'^[a-zA-Z0-9-]+$', slug_raw):
                        film_slug = slug_raw
                    else:
                        film_slug = None
                        
                    film_name = container.get("data-item-name")  # "Title (Year)"
                    
                    year = None
                    if film_name and "(" in film_name:
                        try:
                            year_str = film_name.split("(")[-1].replace(")", "")
                            year = int(year_str)
                        except (ValueError, IndexError):
                            pass
                            
                    if film_slug:
                        films.append({
                            "film_slug": film_slug,
                            "year": year
                        })
                        
                # Old structure (li.poster-container)
                else:
                    div_poster = container.find("div", class_="film-poster")
                    if div_poster:
                        film_slug = div_poster.get("data-film-slug")
                        film_year = div_poster.get("data-film-release-year")
                        
                        if film_slug:
                            films.append({
                                "film_slug": film_slug,
                                "year": int(film_year) if film_year else None
                            })

            logger.info(f"Found {len(films)} films in watchlist scrape")
            
            if not films:
                logger.warning("No films found. Dumping HTML snippet for debugging:")
                logger.warning(soup.prettify()[:1000])
                
            return films
            
        except Exception as e:
            logger.error(f"Error scraping watchlist: {e}")
            return []

    async def get_tmdb_id(self, film_slug: str) -> Optional[int]:
        """
        Visits the Letterboxd film page to extract the authoritative TMDB ID.
        This avoids ambiguity with Title+Year matching.
        """
        url = f"https://letterboxd.com/film/{film_slug}/"
        logger.info(f"Fetching TMDB ID for slug: {film_slug}")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.headers)
                
            if response.status_code != 200:
                logger.warning(f"Failed to fetch film page {film_slug}: {response.status_code}")
                return None
                
            soup = BeautifulSoup(response.text, "html.parser")
            
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
            
        except Exception as e:
            logger.error(f"Error fetching TMDB ID for {film_slug}: {e}")
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