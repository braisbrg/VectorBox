import asyncio
import logging
import os
import random
import re
from typing import Dict, List, Optional

import httpx
import redis.asyncio as aioredis
from bs4 import BeautifulSoup

from services.letterboxd_slug_cache import get_cached_tmdb_id, set_cached_tmdb_id

logger = logging.getLogger(__name__)


# Realistic browser headers. Cloudflare also fingerprints TLS — the
# `curl_cffi` impersonation in `_fetch_with_curl_cffi` is what actually
# bypasses the JS challenge; these headers just keep the L7 surface boring.
LETTERBOXD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# Letterboxd publishes no rate limit. 1 req/s sequential with jitter is the
# B-37 convention — saves IPs from being banned while costing seconds.
LETTERBOXD_MIN_DELAY_S = 1.0
_BACKOFF_STATUS = {429, 403, 503}

# Slug year recovery: Letterboxd disambiguates same-title films by suffixing
# the slug with `-{year}` and, when multiple share that year too, an extra
# `-{n}` counter (e.g. `obsession-2025-2` = the second Obsession of 2025).
# This regex extracts the year ONLY when it appears as a 4-digit trailing
# segment within a plausible range, ignoring sequel suffixes like
# `the-devil-wears-prada-2` (single digit, not a year).
_SLUG_YEAR_RE = re.compile(r"-((?:19|20)\d{2})(?:-\d+)?$")


def _year_from_slug(slug: str) -> Optional[int]:
    """Extract the disambiguating year from a Letterboxd slug, or None.
    Used as a fallback when `data-item-name` didn't yield a year (old poster
    layout) so downstream TMDB-search calls can still filter by year."""
    if not slug:
        return None
    m = _SLUG_YEAR_RE.search(slug)
    if not m:
        return None
    try:
        y = int(m.group(1))
    except ValueError:
        return None
    if 1900 <= y <= 2099:
        return y
    return None


# 2026-05 history: Letterboxd deprecated the legacy `/films/ajax/popular/
# this/week/` fragment (now 404) and the public `/films/popular/this/week/`
# page became a React shell that hydrates client-side. A HAR capture
# revealed the new hydration endpoint at `/csi/films/films-browser-list/
# popular/this/week/?esiAllowFilters=true` (csi = Client-Side Include),
# which still returns the SAME `li.posteritem` + `data-item-slug` HTML
# we used to parse — so curl_cffi + the right Sec-Fetch-* headers + a
# warm-up GET to seed the CSRF cookie is enough; no Playwright required.
# Flip this to False if Letterboxd ever closes the CSI endpoint too.
LETTERBOXD_POPULAR_AVAILABLE = True
LETTERBOXD_POPULAR_URL = "https://letterboxd.com/csi/films/films-browser-list/popular/this/week/?esiAllowFilters=true"


async def _jittered_sleep(base: float = LETTERBOXD_MIN_DELAY_S) -> None:
    """Sleep base * U(0.8, 1.4). Adds entropy so request cadence is not
    a robotic ping every exactly N seconds — anti-bot systems pattern-match
    on regularity, not magnitude."""
    await asyncio.sleep(random.uniform(0.8, 1.4) * base)


class ScraperService:
    """All HTTP traffic to letterboxd.com lives here. Every request goes
    through `_fetch_with_curl_cffi` (Chrome TLS fingerprint) — plain `httpx`
    is reserved as a last-ditch fallback if `curl_cffi` is unavailable.
    """

    def __init__(self):
        self.headers = LETTERBOXD_HEADERS
        self._redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        self._redis: Optional[aioredis.Redis] = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            # redis-py 4.2+: from_url is sync, do NOT await.
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.close()
            except Exception:
                pass
            self._redis = None

    async def aclose(self) -> None:
        """Canonical full-cleanup alias — matches the other service clients."""
        await self.close()

    # ------------------------------------------------------------------
    # HTTP — single source of truth for every letterboxd.com request
    # ------------------------------------------------------------------
    async def _fetch_with_curl_cffi(
        self,
        url: str,
        referer: Optional[str] = None,
        ajax: bool = False,
        max_attempts: int = 3,
    ) -> Optional[str]:
        """Fetch a Letterboxd URL impersonating Chrome 120 at the TLS layer.

        Returns HTML body on 200, None on 404. On 429/403/503 backs off
        exponentially (cap 60s) up to `max_attempts`. Falls back to plain
        httpx only if `curl_cffi` is not installed in the image.
        """
        headers = dict(self.headers)
        if referer:
            headers["Referer"] = referer
        if ajax:
            headers["X-Requested-With"] = "XMLHttpRequest"

        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            logger.warning("curl_cffi not installed — falling back to httpx (Cloudflare may block)")
            return await self._fetch_with_httpx(url, headers)

        async with curl_requests.AsyncSession() as session:
            for attempt in range(max_attempts):
                try:
                    resp = await session.get(
                        url, headers=headers, impersonate="chrome120", timeout=15
                    )
                except Exception as e:
                    if attempt + 1 >= max_attempts:
                        logger.warning(f"[scraper] curl_cffi exhausted for {url}: {e}")
                        return None
                    backoff = min(60.0, 2 ** attempt)
                    logger.info(f"[scraper] curl_cffi attempt {attempt+1} failed for {url}: {e} — sleeping {backoff}s")
                    await asyncio.sleep(backoff)
                    continue

                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 404:
                    return None
                if resp.status_code in _BACKOFF_STATUS and attempt + 1 < max_attempts:
                    backoff = min(60.0, 2 ** attempt)
                    logger.info(f"[scraper] {url} -> {resp.status_code}, backing off {backoff}s (attempt {attempt+1}/{max_attempts})")
                    await asyncio.sleep(backoff)
                    continue
                logger.warning(f"[scraper] {url} -> {resp.status_code}")
                return None
        return None

    async def _fetch_with_httpx(self, url: str, headers: Dict[str, str]) -> Optional[str]:
        """Last-ditch fallback when curl_cffi is missing. Cloudflare will
        usually block this for letterboxd.com."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers, follow_redirects=True)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 404:
                    return None
                logger.warning(f"[scraper-httpx] {url} -> {resp.status_code}")
                return None
        except Exception as e:
            logger.error(f"[scraper-httpx] {url}: {e}")
            return None

    # ------------------------------------------------------------------
    # Listing pages (watchlist + likes) — same HTML structure, one parser
    # ------------------------------------------------------------------
    async def _scrape_listing_page(
        self,
        username: str,
        path_suffix: str,
        page: int,
    ) -> tuple[list[dict], bool]:
        """Scrape one page of any Letterboxd poster-list (watchlist, likes,
        any other `/{user}/{path_suffix}/page/N/`).

        Returns (films, has_more). has_more=False signals end of pagination
        (404 or empty page).
        """
        base = f"https://letterboxd.com/{username}/{path_suffix}/"
        url = base if page == 1 else f"{base}page/{page}/"
        logger.info(f"Scraping {path_suffix} page {page} for {username}: {url}")

        html = await self._fetch_with_curl_cffi(url, referer=f"https://letterboxd.com/{username}/")
        if not html:
            return [], False

        soup = BeautifulSoup(html, "html.parser")
        poster_containers = soup.find_all("div", attrs={"data-component-class": "LazyPoster"})
        if not poster_containers:
            poster_containers = soup.find_all("li", class_="poster-container")

        films = self._parse_poster_containers(poster_containers)
        return films, bool(films)

    async def _scrape_watchlist_page(self, username: str, page: int) -> tuple[list[dict], bool]:
        """Back-compat wrapper. New callers should use `_scrape_listing_page`."""
        return await self._scrape_listing_page(username, "watchlist", page)

    async def scrape_watchlist_all(self, username: str, max_pages: int = 50) -> List[dict]:
        """Scrape every page of a user's watchlist (sequential, jittered)."""
        return await self._scrape_listing_all(username, "watchlist", max_pages)

    async def scrape_user_likes(self, username: str, max_pages: int = 20) -> List[dict]:
        """Scrape liked films from `/{user}/likes/films/` for B-38 group-sync
        enrichment. Same HTML structure as watchlist; smaller default cap
        because the centroid signal stabilizes well before 1400 films."""
        return await self._scrape_listing_all(username, "likes/films", max_pages)

    async def _scrape_listing_all(self, username: str, path_suffix: str, max_pages: int) -> List[dict]:
        all_films: list[dict] = []
        seen: set[str] = set()
        last_page = 0
        for page in range(1, max_pages + 1):
            last_page = page
            films, has_more = await self._scrape_listing_page(username, path_suffix, page)
            for f in films:
                slug = f.get("film_slug")
                if slug and slug not in seen:
                    seen.add(slug)
                    all_films.append(f)
            if not has_more:
                break
            if page < max_pages:
                await _jittered_sleep()
        logger.info(
            f"{path_suffix} scrape complete for {username}: {len(all_films)} films across {last_page} pages"
        )
        return all_films

    async def scrape_watchlist_recent(self, username: str) -> List[dict]:
        """Legacy first-page-only scrape. New callers should prefer
        `scrape_watchlist_all`."""
        films, _ = await self._scrape_listing_page(username, "watchlist", 1)
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
                    # Fallback: if data-item-name didn't include the year, try
                    # to recover it from the slug suffix so downstream fuzzy
                    # TMDB search can still pass year= to narrow matches.
                    if year is None:
                        year = _year_from_slug(film_slug)
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
                    parsed_year: Optional[int] = None
                    if film_year:
                        try:
                            parsed_year = int(film_year)
                        except ValueError:
                            parsed_year = None
                    if parsed_year is None and film_slug:
                        parsed_year = _year_from_slug(film_slug)
                    if film_slug:
                        films.append({
                            "film_slug": film_slug,
                            "year": parsed_year,
                            "title": None,
                        })

        return films

    # ------------------------------------------------------------------
    # Single-film resolution (slug -> tmdb_id) with Redis cache
    # ------------------------------------------------------------------
    async def get_tmdb_id(self, film_slug: str) -> Optional[int]:
        """Resolve a Letterboxd slug to a TMDB ID.

        Cache-first: a slug we've successfully resolved before never hits
        the network again for 30 days. A slug we *failed* to resolve is
        cached as MISS for 7 days so a single bad film doesn't keep
        thrashing the scrape path on every re-sync.
        """
        if not film_slug:
            return None
        r = await self._get_redis()
        cached = await get_cached_tmdb_id(film_slug, r)
        if isinstance(cached, int):
            return cached
        if cached == "MISS":
            return None

        url = f"https://letterboxd.com/film/{film_slug}/"
        html = await self._fetch_with_curl_cffi(url)
        if not html:
            # Don't cache — could be transient. Let the next pass try again.
            return None

        soup = BeautifulSoup(html, "html.parser")
        tmdb_id: Optional[int] = None

        # Letterboxd usually puts the TMDB ID on the body: data-tmdb-id="..."
        body = soup.find("body")
        if body and body.has_attr("data-tmdb-id"):
            try:
                tmdb_id = int(body["data-tmdb-id"])
            except ValueError:
                pass

        # Fallback: link to themoviedb.org
        if tmdb_id is None:
            tmdb_link = soup.find("a", href=lambda href: href and "themoviedb.org/movie/" in href)
            if tmdb_link:
                try:
                    href = tmdb_link["href"]
                    parts = href.split("themoviedb.org/movie/")
                    if len(parts) > 1:
                        id_part = parts[1].split("/")[0].split("?")[0]
                        tmdb_id = int(id_part)
                except (ValueError, IndexError, KeyError):
                    pass

        if tmdb_id is None:
            # We got the page but couldn't find a TMDB ID — that IS a genuine
            # negative we should cache so we stop retrying.
            logger.warning(f"Could not find TMDB ID for {film_slug}")
            await set_cached_tmdb_id(film_slug, None, r)
            return None

        logger.info(f"Resolved {film_slug} -> tmdb_id={tmdb_id} (caching 30d)")
        await set_cached_tmdb_id(film_slug, tmdb_id, r)
        return tmdb_id

    async def set_resolved_tmdb_id(self, film_slug: str, tmdb_id: Optional[int]) -> None:
        """Public hook for callers that resolved a slug via a different path
        (e.g. the RSS router's fuzzy TMDB-search fallback). Stores positive
        or negative result in the same Redis cache so the next sync skips
        the work."""
        if not film_slug:
            return
        r = await self._get_redis()
        await set_cached_tmdb_id(film_slug, tmdb_id, r)

    # ------------------------------------------------------------------
    # Popular this week
    # ------------------------------------------------------------------
    async def scrape_popular_this_week(self) -> List[Dict]:
        """Fetch Letterboxd's 'Popular This Week' CSI fragment. Returns a
        list of {title, year, letterboxd_slug, letterboxd_rating}.

        Hits the `/csi/films/films-browser-list/popular/this/week/` endpoint
        — the React app's XHR target — within a single curl_cffi session so
        the CSRF cookie set by the warm-up GET to `letterboxd.com/` is
        carried into the CSI request (the endpoint refuses calls that don't
        present it). Headers mimic what the browser sends as a same-origin
        CORS XHR (`Sec-Fetch-Mode: cors`, `Accept: */*`).
        """
        if not LETTERBOXD_POPULAR_AVAILABLE:
            logger.info("[scraper] popular-this-week disabled — caller will fall back to Trakt")
            return []

        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            logger.warning("[scraper] curl_cffi missing — cannot hit Letterboxd /csi/, falling back to Trakt")
            return []

        html: Optional[str] = None
        try:
            async with curl_requests.AsyncSession() as session:
                # Warm-up: seeds com.xk72.webparts.csrf in the session jar.
                # The /csi/ endpoint rejects requests without it.
                warmup = await session.get(
                    "https://letterboxd.com/",
                    headers=self.headers,
                    impersonate="chrome120",
                    timeout=15,
                )
                if warmup.status_code != 200:
                    logger.warning(f"[scraper] popular warmup -> {warmup.status_code}")
                    return []

                csi_headers = dict(self.headers)
                csi_headers["Accept"] = "*/*"  # CSI returns HTML fragments, not full docs
                csi_headers["Referer"] = "https://letterboxd.com/films/popular/this/week/"
                csi_headers["Sec-Fetch-Dest"] = "empty"
                csi_headers["Sec-Fetch-Mode"] = "cors"
                csi_headers["Sec-Fetch-Site"] = "same-origin"

                resp = await session.get(
                    LETTERBOXD_POPULAR_URL,
                    headers=csi_headers,
                    impersonate="chrome120",
                    timeout=15,
                )
                if resp.status_code != 200:
                    logger.warning(f"[scraper] popular /csi/ -> {resp.status_code}")
                    return []
                html = resp.text
        except Exception as e:
            logger.warning(f"[scraper] popular fetch failed: {e}")
            return []

        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("li.posteritem")
        popular_movies: List[Dict] = []
        for item in items:
            try:
                rating_str = item.get("data-average-rating")
                rating = float(rating_str) if rating_str else 0.0

                div = item.select_one("div.react-component")
                if not div:
                    continue

                slug = div.get("data-item-slug")
                name_year = div.get("data-item-name")  # "Wicked (2024)"
                if not slug or not name_year:
                    continue

                m = re.match(r"(.*)\s\((\d{4})\)$", name_year)
                if m:
                    title = m.group(1)
                    year = int(m.group(2))
                else:
                    title = name_year
                    year = None

                popular_movies.append({
                    "title": title,
                    "year": year,
                    "letterboxd_slug": slug,
                    "letterboxd_rating": rating,
                })
            except Exception as e:
                logger.warning(f"Error parsing popular movie item: {e}")
                continue

        logger.info(f"Found {len(popular_movies)} popular films")
        return popular_movies

    async def scrape_popular_this_week_resolved(self) -> List[Dict]:
        """Same as `scrape_popular_this_week` but yields resolved items
        `{tmdb_id, letterboxd_rating}`. The Letterboxd rating (already in
        the CSI payload as `data-average-rating`) is preserved so consumers
        can use it as a curation / ordering signal — currently a quality
        filter in the engine's popular section.

        Slug resolution goes through the 30d Redis cache so a repeat run
        does ~0 work for the resolution step (5ms per slug on cache hit).
        """
        items = await self.scrape_popular_this_week()
        resolved: List[Dict] = []
        for it in items:
            slug = it.get("letterboxd_slug")
            if not slug:
                continue
            tid = await self.get_tmdb_id(slug)
            if tid is not None:
                resolved.append({
                    "tmdb_id": tid,
                    "letterboxd_rating": it.get("letterboxd_rating"),
                })
            # Jitter between film-page scrapes when the slug wasn't cached.
            # (cached resolutions return <5ms so this is a no-op for them.)
            await asyncio.sleep(random.uniform(0.2, 0.4))
        return resolved

    async def get_popular_with_fallback(self, min_items: int = 20) -> tuple[List[Dict], str]:
        """Letterboxd-first, Trakt-fallback. Returns (items, source).

        Each item is `{tmdb_id, letterboxd_rating}`. Trakt-sourced items
        carry `letterboxd_rating=None` because there's no equivalent signal
        outside Letterboxd — downstream consumers must treat None as
        "unknown, do not filter on it".

        `source` is "letterboxd" / "trakt" / "letterboxd_degraded" —
        useful for orchestrator stats and drift alerting.
        """
        items = await self.scrape_popular_this_week_resolved()
        if len(items) >= min_items:
            return items, "letterboxd"

        logger.warning(
            f"[scraper] Letterboxd popular returned {len(items)} resolved items "
            f"< {min_items} threshold — falling back to Trakt /movies/trending"
        )
        try:
            from services.trakt_client import TraktClient
            trakt = TraktClient()
            try:
                movies = await trakt.trending(limit=50)
            finally:
                await trakt.aclose()
        except Exception as e:
            logger.error(f"[scraper] Trakt fallback failed: {e}")
            return items, "letterboxd_degraded"

        fallback_items: List[Dict] = []
        for m in movies:
            ids_block = m.get("ids") if isinstance(m, dict) else None
            if isinstance(ids_block, dict):
                tid = ids_block.get("tmdb")
                if isinstance(tid, int):
                    fallback_items.append({
                        "tmdb_id": tid,
                        "letterboxd_rating": None,
                    })
        logger.info(f"[scraper] Trakt fallback produced {len(fallback_items)} TMDB IDs")
        return fallback_items, "trakt"
