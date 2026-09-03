"""
TMDB API Client with Redis caching and rate limiting
Security: API key protection, rate limiting, input sanitization
"""
import httpx
import redis.asyncio as redis
import json
import orjson
import asyncio
import os
import random
from typing import Optional, Dict, List
from datetime import timedelta
import logging

# Same floor the enricher refuses below — if these two drift, this client hands it
# text it will always reject. cinematic_enricher imports only llm_models, so no cycle.
from services.cinematic_enricher import MIN_OVERVIEW_CHARS
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Los dos idiomas de la interfaz, en el formato que TMDB espera. El inglés no
# está: es el valor por defecto de la API, así que pedirlo es una clave de caché
# de más para la misma respuesta.
TMDB_LANGS = {"es": "es-ES"}


def pick_fallback_overview(
    original_language: Optional[str], usable_overviews: Dict[str, str]
) -> tuple[Optional[str], Optional[str]]:
    """Choose a synopsis when TMDB has none in English. Returns (lang_code, text).

    Order: the film's own language (closest to how it was written), then Spanish
    (the other UI locale), then anything at all — any real synopsis beats none,
    because the enricher turns it into English prose either way.
    """
    for code in (original_language, "es", *usable_overviews):
        if code and code in usable_overviews:
            return code, usable_overviews[code]
    return None, None


class TMDBClient:
    """TMDB API wrapper with intelligent caching, rate limiting, and connection pooling"""
    
    RATE_LIMIT_DELAY = 0.25  # 4 requests/second
    
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.api_key = os.getenv("TMDB_API_KEY")
        self.read_token = os.getenv("TMDB_READ_TOKEN")
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis_client: Optional[redis.Redis] = None
        self.last_request_time = 0
        
        # [RESILIENCE] Circuit Breaker
        self.cb_state = "CLOSED" # CLOSED, OPEN, HALF-OPEN
        self.cb_failure_count = 0
        self.cb_threshold = 3
        self.cb_reset_timeout = 60 # seconds
        self.cb_last_failure_time = 0
        
        self.base_url = "https://api.themoviedb.org/3"
        
        self.headers = {
            "accept": "application/json",
            "User-Agent": "VectorBox/1.0" # Keep User-Agent
        }
        
        if self.read_token:
            self.headers["Authorization"] = f"Bearer {self.read_token}"
        elif self.api_key:
            # Fallback to query param if needed, but modern TMDB prefers Bearer
            pass
        else:
            logger.warning("No TMDB API Key or Read Token found in environment!")
            
        self._external_client = client
        
        # [INFRASTRUCTURE RESILIENCE] Connection Pooling
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=40)
        timeout = httpx.Timeout(20.0, connect=5.0)

        self.client = client if client else httpx.AsyncClient(
            base_url=self.base_url, # Use self.base_url
            headers=self.headers,
            limits=limits,
            timeout=timeout,
            http2=True,
            verify=True
        )
        self._lock = asyncio.Lock() # Added lock
    
    async def close(self):
        """Full cleanup: Redis + httpx (whichever this client owns). `aclose()` is an alias.

        Previously closed only httpx, so callers using close() leaked the Redis
        connection — Redis is now folded in so both methods do the same thing.
        """
        if self.redis_client:
            await self.redis_client.close()
            self.redis_client = None
        if not self._external_client and self.client:
            await self.client.aclose()
    
    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection"""
        if not self.redis_client:
            self.redis_client = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
        return self.redis_client
    
    async def _rate_limit(self):
        """Enforce rate limiting to respect TMDB API limits"""
        current_time = asyncio.get_running_loop().time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.RATE_LIMIT_DELAY:
            await asyncio.sleep(self.RATE_LIMIT_DELAY - time_since_last)
        
        self.last_request_time = asyncio.get_running_loop().time()
    
    async def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make HTTP request with error handling and rate limiting and retry logic"""
        # [RESILIENCE] Circuit Breaker Check
        current_time = asyncio.get_running_loop().time()
        if self.cb_state == "OPEN":
            if current_time - self.cb_last_failure_time > self.cb_reset_timeout:
                logger.info("TMDB Circuit Breaker entering HALF-OPEN state.")
                self.cb_state = "HALF-OPEN"
            else:
                # Fail fast
                logger.warning(f"TMDB Circuit Breaker OPEN, failing fast for {endpoint}")
                return None

        await self._rate_limit()
        
        # [ZERO-LEAK] params passed per-request. Headers already set in __init__.
        
        retries = 3
        base_delay = 1.0 # seconds
        
        for attempt in range(retries):
            try:
                response = await self.client.get(endpoint, params=params)
                
                if response.status_code == 429: # Rate limited
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    logger.warning(f"TMDB Rate limit hit for {endpoint}. Retrying in {delay:.2f}s... (Attempt {attempt+1}/{retries})")
                    await asyncio.sleep(delay)
                    continue # Try again
                
                response.raise_for_status() # Raise for other HTTP errors (4xx, 5xx)
                
                # Success - Reset Circuit Breaker
                if self.cb_state != "CLOSED":
                    logger.info("TMDB Circuit Breaker recovered (CLOSED).")
                    self.cb_state = "CLOSED"
                    self.cb_failure_count = 0
                
                # [PERFORMANCE] orjson Parsing
                return orjson.loads(response.content)
                
            except httpx.HTTPStatusError as e:
                logger.error(f"TMDB API error {e.response.status_code}: {endpoint}")
                if e.response.status_code == 429:  # Rate limited
                    logger.warning("TMDB rate limit hit, backing off...")
                    await asyncio.sleep(2)
                elif e.response.status_code in (502, 503, 504):
                    # Gateway/unavailable errors are TMDB-edge hiccups — same class
                    # as the transport errors below. They spike when the watchlist
                    # fires a burst of parallel /watch/providers fetches and TMDB's
                    # CDN briefly 502s; that is NOT "TMDB is down", so don't trip the
                    # breaker (tripping it made one bad film blank out providers for
                    # the whole list via the cached-fallback path).
                    logger.warning(f"TMDB transient gateway error {e.response.status_code} on {endpoint} — not tripping breaker")
                elif e.response.status_code >= 500:
                     # Genuine server error (500/etc.) - Trip Circuit Breaker
                     self._record_failure()
                return None
            except orjson.JSONDecodeError as e:
                logger.error(f"JSON Parse Error: {e}")
                return None
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
                # Transient transport errors — don't trip the circuit breaker.
                # These are network hiccups (esp. with HTTP/2), not "TMDB is down".
                logger.warning(f"TMDB transient transport error ({type(e).__name__}) on {endpoint}: {e or '<no message>'}")
                return None
            except Exception as e:
                logger.error(f"TMDB request failed ({type(e).__name__}) on {endpoint}: {e or '<no message>'}")
                self._record_failure()
                return None

    def _record_failure(self):
        """Record a failure and potentially trip the circuit breaker"""
        current_time = asyncio.get_running_loop().time()
        self.cb_failure_count += 1
        self.cb_last_failure_time = current_time
        
        if self.cb_failure_count >= self.cb_threshold:
            if self.cb_state != "OPEN":
                self.cb_state = "OPEN"
                logger.critical("CIRCUIT OPEN: TMDB API is unresponsive. Falling back to cached data.")
    
    async def search_movie(self, title: str, year: Optional[int] = None) -> Optional[Dict]:
        """
        Search for a movie by title and optional year
        Security: Input sanitization via URL encoding
        """
        # Security: Sanitize title (httpx handles URL encoding)
        title = title.strip()[:500]  # Limit length
        
        cache_key = f"tmdb:search:{title}:{year or 'any'}"
        r = await self._get_redis()
        
        # Check cache first (7 days TTL)
        cached = await r.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for movie search: {title}")
            return orjson.loads(cached)
        
        # Make API request
        params = {"query": title, "include_adult": "false"}
        if year:
            params["year"] = year
        
        data = await self._make_request("/search/movie", params)
        
        if data and data.get("results"):
            result = data["results"][0]  # Best match
            await r.setex(cache_key, timedelta(days=7), orjson.dumps(result))
            return result
        
        return None
    
    async def get_person(self, name: str, lang: str = "en") -> Optional[Dict]:
        """Foto y biografía de una persona por nombre, o None.

        Dos llamadas porque TMDB las separa: `/search/person` da el id y la foto,
        `/person/{id}` la biografía. Se cachea 30 días — una biografía cambia
        menos que una película, y esto sólo se pide al abrir una ficha.

        La bio se pide en el idioma pedido y cae a inglés si TMDB la devuelve
        vacía, que pasa a menudo: medido 2026-08-04, existe en español para 5 de
        5 directores probados, pero algunas son un muñón (Spielberg: 281 caracteres
        frente a 3390 en inglés). Un muñón real se prefiere igualmente a un texto
        que el lector no entiende; sólo el vacío justifica el salto.

        Devuelve None en silencio si no hay coincidencia: una ficha de director
        sin foto sigue siendo una ficha útil, y la filmografía sale de nuestra
        base de datos, no de aquí.
        """
        name = (name or "").strip()[:200]
        if not name:
            return None

        # El idioma va en la clave: sin él, el primer lector en español dejaría
        # su bio cacheada 30 días para todos los que vengan en inglés.
        cache_key = f"tmdb:person:{lang}:{name.lower()}"
        r = await self._get_redis()
        cached = await r.get(cache_key)
        if cached:
            return orjson.loads(cached)

        found = await self._make_request("/search/person", {"query": name, "include_adult": "false"})
        results = (found or {}).get("results") or []
        # Coincidencia EXACTA de nombre: buscar "Anderson" devuelve una lista larga
        # y quedarse con el primero pondría la foto de otra persona en la ficha,
        # que es peor que no poner ninguna.
        match = next((p for p in results if (p.get("name") or "").lower() == name.lower()), None)
        if not match:
            return None

        params = {"language": TMDB_LANGS[lang]} if lang in TMDB_LANGS else {}
        detail = await self._make_request(f"/person/{match['id']}", params) or {}
        bio = (detail.get("biography") or "").strip()
        if not bio and params:
            fallback = await self._make_request(f"/person/{match['id']}", {}) or {}
            bio = (fallback.get("biography") or "").strip()
        person = {
            "tmdb_id": match["id"],
            "name": match.get("name") or name,
            "profile_path": match.get("profile_path"),
            "biography": bio or None,
            "birthday": detail.get("birthday"),
            "place_of_birth": detail.get("place_of_birth"),
        }
        await r.setex(cache_key, timedelta(days=30), orjson.dumps(person))
        return person

    async def get_movie_details(self, tmdb_id: int, force_refresh: bool = False) -> Optional[Dict]:
        """
        Get detailed movie information
        Cache: 7 days
        """
        cache_key = f"tmdb:movie:{tmdb_id}"
        r = await self._get_redis()
        
        if not force_refresh:
            cached = await r.get(cache_key)
            if cached:
                logger.debug(f"Cache hit for movie {tmdb_id}")
                return orjson.loads(cached)
        
        # Phase 13: Data Enrichment - Fetch keywords, credits, translations, and providers in single call
        params = {"append_to_response": "keywords,credits,translations,watch/providers,release_dates"}
        data = await self._make_request(f"/movie/{tmdb_id}", params=params)
        
        if data:
            # Process keywords into flat list
            if "keywords" in data and "keywords" in data["keywords"]:
                data["keywords_flat"] = [k["name"] for k in data["keywords"]["keywords"]]
            else:
                data["keywords_flat"] = []
                
            # Process Directors from credits
            data["directors"] = []
            if "credits" in data and "crew" in data["credits"]:
                data["directors"] = [
                    member["name"] 
                    for member in data["credits"]["crew"] 
                    if member.get("job") == "Director"
                ]
            
            # Process Cast from credits (Top 3)
            data["cast"] = []
            if "credits" in data and "cast" in data["credits"]:
                sorted_cast = sorted(data["credits"]["cast"], key=lambda x: x.get("order", 999))
                data["cast"] = [member["name"] for member in sorted_cast[:3]]

            # Phase 12: Fetch Spanish metadata (Parsed from appended translations)
            title_es = None
            overview_es = None
            
            usable_overviews: Dict[str, str] = {}

            if "translations" in data and "translations" in data["translations"]:
                for t in data["translations"]["translations"]:
                    t_data = t.get("data", {}) or {}
                    t_overview = (t_data.get("overview") or "").strip()
                    if len(t_overview) >= MIN_OVERVIEW_CHARS:
                        usable_overviews[t.get("iso_639_1")] = t_overview
                    if t.get("iso_639_1") == "es":
                        title_es = t_data.get("title")
                        overview_es = t_data.get("overview")

            # Use English as fallback for comparison to avoid redundant updates if identical
            if title_es and title_es != data.get("title"):
                data["title_es"] = title_es
            if overview_es and overview_es != data.get("overview"):
                data["overview_es"] = overview_es

            # TMDB serves en-US by default, and plenty of non-English films have no English
            # synopsis at all — 47 of the 76 unenrichable films in the catalogue had one
            # waiting in the translations we were ALREADY downloading here (measured
            # 2026-08-06). Left empty, the film is refused by the enricher and never enters
            # gated recommendations. Original language first, then Spanish, then whatever
            # exists: the enricher emits English prose regardless of the source language.
            if len((data.get("overview") or "").strip()) < MIN_OVERVIEW_CHARS:
                code, text = pick_fallback_overview(data.get("original_language"), usable_overviews)
                if text:
                    data["overview"] = text
                    logger.info(
                        f"No en-US synopsis for tmdb_id={tmdb_id}; using '{code}' translation"
                    )

            # Extract Watch Providers (Raw, to be processed by service)
            # We don't process them here to keep client focused on fetching, 
            # but we ensure the key exists for the consumer.
            if "watch/providers" in data and "results" in data["watch/providers"]:
                data["providers_data"] = data["watch/providers"]["results"]

            await r.setex(cache_key, timedelta(days=7), orjson.dumps(data))
        
        return data
    
    async def get_movie_recommendations(self, tmdb_id: int, page: int = 1) -> List[Dict]:
        """
        Get similar movies based on TMDB's collaborative filtering algorithm.
        This provides high-quality "More Like This" recommendations.
        """
        cache_key = f"tmdb:recommendations:{tmdb_id}:{page}"
        r = await self._get_redis()
        
        cached = await r.get(cache_key)
        if cached:
            return orjson.loads(cached)
            
        data = await self._make_request(f"/movie/{tmdb_id}/recommendations", {"page": page})
        
        if data and data.get("results"):
            results = data["results"]
            await r.setex(cache_key, timedelta(days=3), orjson.dumps(results))
            return results
            
        return []
    


    async def get_watch_providers(self, tmdb_id: int, country: str = "ES") -> Optional[Dict]:
        """
        Get streaming availability (JustWatch data via TMDB)
        Cache: 24 hours (availability changes frequently)
        Security: Validate country code format
        """
        # Security: Validate country code (2-letter ISO)
        if not country or len(country) != 2 or not country.isalpha():
            country = "ES"
        country = country.upper()
        
        cache_key = f"tmdb:providers:{tmdb_id}:{country}"
        r = await self._get_redis()
        
        cached = await r.get(cache_key)
        if cached:
            return orjson.loads(cached)
        
        data = await self._make_request(f"/movie/{tmdb_id}/watch/providers")
        
        if data and data.get("results"):
            # Extract country-specific providers
            country_data = data["results"].get(country, {})
            await r.setex(cache_key, timedelta(hours=24), orjson.dumps(country_data))
            return country_data
        
        return None
    
    async def get_movie_keywords(self, tmdb_id: int) -> List[str]:
        """Get movie keywords for enhanced embeddings"""
        cache_key = f"tmdb:keywords:{tmdb_id}"
        r = await self._get_redis()
        
        cached = await r.get(cache_key)
        if cached:
            return orjson.loads(cached)
        
        data = await self._make_request(f"/movie/{tmdb_id}/keywords")
        
        keywords = []
        if data and data.get("keywords"):
            keywords = [kw["name"] for kw in data["keywords"][:10]]  # Limit to 10
            await r.setex(cache_key, timedelta(days=7), orjson.dumps(keywords))
        
        return keywords
    
    async def get_release_dates(self, tmdb_id: int) -> dict:
        """
        Fetch release dates by country from TMDB.
        Returns dict with 'us', 'es', 'ww' keys (theatrical releases only, type=3).
        """
        data = await self._make_request(f"/movie/{tmdb_id}/release_dates")
        if not data:
            return {}

        result = {}
        for country_result in data.get("results", []):
            iso = country_result.get("iso_3166_1", "").upper()
            if iso not in ("US", "ES"):
                continue
            for release in country_result.get("release_dates", []):
                if release.get("type") == 3:  # theatrical only
                    date_str = release.get("release_date", "")[:10]
                    if date_str:
                        if iso == "US":
                            result["us"] = date_str
                        elif iso == "ES":
                            result["es"] = date_str

        return result

    async def get_collection(self, collection_id: int) -> Optional[Dict]:
        """Fetch all films in a TMDB collection (saga). Returns the collection
        dict with `parts: [...]` listing every movie, or None on error.
        Cache: 7 days (collections add new films rarely)."""
        cache_key = f"tmdb:collection:{collection_id}"
        r = await self._get_redis()
        cached = await r.get(cache_key)
        if cached:
            return orjson.loads(cached)
        data = await self._make_request(f"/collection/{collection_id}")
        if data:
            await r.setex(cache_key, timedelta(days=7), orjson.dumps(data))
        return data

    async def discover_movies(
        self,
        with_genres: Optional[List[int]] = None,
        without_genres: Optional[List[int]] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        vote_average_min: Optional[float] = None,
        vote_average_max: Optional[float] = None,
        vote_count_min: Optional[int] = 100,  # Filter out obscure movies
        with_runtime_min: Optional[int] = None,
        with_runtime_max: Optional[int] = None,
        sort_by: str = "vote_average.desc",
        page: int = 1,
        primary_release_date_gte: Optional[str] = None,
        primary_release_date_lte: Optional[str] = None,
        with_original_language: Optional[str] = None,
        with_companies: Optional[str] = None,
        region: Optional[str] = None,
        with_release_type: Optional[str] = None,
        release_date_gte: Optional[str] = None,
        release_date_lte: Optional[str] = None,
    ) -> List[Dict]:
        """
        Discover movies using TMDB's Discover API.
        Returns movies from the global TMDB database based on filters.

        `region` + `release_date.*` filtran por la fecha de estreno EN ESE PAÍS, no por
        `primary_release_date`, que es la mundial. La diferencia no es sutil: una película
        extranjera que llega a los cines españoles hoy puede tener su fecha primaria 8-16
        meses atrás (Jumbo 2025-03-31 → cines ES 2026-07-24), así que una ventana de 90
        días sobre la primaria no la ve nunca. `with_release_type=3` = estreno en cine.
        """
        params = {
            "api_key": self.api_key,
            "language": "en-US",
            "sort_by": sort_by,
            "page": page,
        }
        if vote_count_min is not None:
            params["vote_count.gte"] = vote_count_min
        
        if with_genres:
            params["with_genres"] = ",".join(map(str, with_genres))
        if without_genres:
            params["without_genres"] = ",".join(map(str, without_genres))
        if year_min:
            params["primary_release_date.gte"] = f"{year_min}-01-01"
        if year_max:
            params["primary_release_date.lte"] = f"{year_max}-12-31"
        if region:
            params["region"] = region
        if with_release_type:
            params["with_release_type"] = with_release_type
        if release_date_gte:
            params["release_date.gte"] = release_date_gte
        if release_date_lte:
            params["release_date.lte"] = release_date_lte
        if vote_average_min:
            params["vote_average.gte"] = vote_average_min
        if vote_average_max:
            params["vote_average.lte"] = vote_average_max
        if with_runtime_min:
            params["with_runtime.gte"] = with_runtime_min
        if with_runtime_max:
            params["with_runtime.lte"] = with_runtime_max
        if primary_release_date_gte:
            params["primary_release_date.gte"] = primary_release_date_gte
        if primary_release_date_lte:
            params["primary_release_date.lte"] = primary_release_date_lte
        if with_original_language:
            params["with_original_language"] = with_original_language
        if with_companies:
            params["with_companies"] = with_companies

        try:
            # Optimized: Use the shared _make_request which uses the connection pool
            # instead of spinning up a new client.
            data = await self._make_request("/discover/movie", params=params)
            return data.get("results", []) if data else []
        except Exception as e:
            logger.error(f"TMDB discover failed: {e}")
            return []
    
    async def get_trending_movies(self, time_window: str = "week", limit: int = 20) -> List[Dict]:
        """
        Get trending movies from TMDB.
        
        Args:
            time_window: "day" or "week"
            limit: Maximum number of movies to return
            
        Returns:
            List of movie dictionaries with id, title, etc.
        """
        cache_key = f"tmdb:trending:{time_window}"
        r = await self._get_redis()
        
        cached = await r.get(cache_key)
        if cached:
            results = orjson.loads(cached)
            return results[:limit]
        
        all_results = []
        pages_needed = (limit // 20) + 1  # TMDB returns 20 per page
        
        for page in range(1, min(pages_needed + 1, 4)):  # Max 3 pages (60 movies)
            data = await self._make_request(f"/trending/movie/{time_window}", {"page": page})
            
            if data and data.get("results"):
                all_results.extend(data["results"])
            else:
                break
        
        if all_results:
            # Cache for 6 hours (trending changes frequently)
            await r.setex(cache_key, timedelta(hours=6), orjson.dumps(all_results))
        
        return all_results[:limit]
    
    async def aclose(self):
        """Canonical alias for close() (full Redis + httpx cleanup)."""
        await self.close()
        logger.info("TMDB client closed")
