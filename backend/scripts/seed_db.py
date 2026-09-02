import asyncio
import os
import re
import sys
import logging
import argparse
import unicodedata
from typing import List, Dict, Optional
from tqdm import tqdm
from sqlalchemy import select

# Add parent directory to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_db, AsyncSessionLocal
from models.database import Movie
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService
from services.omdb_client import OMDbClient
from services.provider_service import ProviderService
from services.movie_factory import MovieFactory
from services.trakt_client import TraktClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("seed_db.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# País cuyos estrenos en CINE lee la estrategia `recent` en su segunda pasada.
# Es el mercado del producto, no una preferencia de usuario: el seed llena el
# catálogo, no personaliza. Ver fetch_recent_movies.
RECENT_REGION = "ES"

# Suppress other loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)


# ---- from_file helpers (TSPDT-style tab-separated lists) ----

_LIST_ARTICLES = (
    "The", "A", "An", "La", "Le", "Les", "L'", "El", "Los", "Las",
    "Il", "Lo", "I", "Gli", "Un", "Une", "Una", "Der", "Die", "Das",
    "De", "Het", "Os", "As", "O",
)
_ARTICLE_RE = re.compile(r"^(.+), (%s)$" % "|".join(re.escape(a) for a in _LIST_ARTICLES))


def fix_mojibake(s: str) -> str:
    """Repair UTF-8 text that was decoded as cp1252 ('BuÃ±uel' -> 'Buñuel').
    Clean text round-trips to invalid UTF-8 and passes through unchanged."""
    try:
        return s.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def deinvert_article(title: str) -> str:
    """'Rules of the Game, The' -> 'The Rules of the Game'; 'Atalante, L'' -> 'L'Atalante'."""
    m = _ARTICLE_RE.match(title)
    if not m:
        return title
    rest, art = m.group(1), m.group(2)
    return art + rest if art.endswith("'") else f"{art} {rest}"


def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def wanted_director_keys(director_field: str) -> set:
    """Alpha-squashed match keys for a list's director field. Handles co-directors
    ('Keaton, Buster & Edward Sedgwick'), 'Surname, Name' inversion, and spelling
    variants via last-token fallback ('González Iñárritu' -> 'inarritu')."""
    keys = set()
    for name in re.split(r"[&/]", director_field):
        surname = ascii_fold(name.split(",")[0].strip())
        if not surname or "various" in surname:
            continue
        for candidate in (surname, surname.split()[-1]):
            squashed = re.sub(r"[^a-z]", "", candidate)
            if len(squashed) >= 3:
                keys.add(squashed)
    return keys


def pick_from_filmography(directed: List[Dict], title: str, year: int) -> Optional[int]:
    """Pick the one film matching title/year inside a director's filmography.
    Title-agnostic on purpose: inside a filmography, year ±2 or an exact squashed
    title is nearly always unique. Ambiguity returns None (CSV, never a guess)."""
    squash = lambda s: re.sub(r"[^a-z0-9]", "", ascii_fold(s or ""))
    want = squash(title)
    candidates = []
    for c in directed:
        rd = (c.get("release_date") or "")[:4]
        year_diff = abs(int(rd) - year) if rd.isdigit() else 99
        title_sq = squash(c.get("title"))
        orig_sq = squash(c.get("original_title"))
        exact = bool(want) and want in (title_sq, orig_sq)
        contains = len(want) >= 5 and (want in title_sq or want in orig_sq)
        t_score = 0 if exact else (1 if contains else 2)
        if t_score == 2 and year_diff > 2:
            continue  # no signal at all
        if t_score < 2 and year_diff > 25:
            # Exact-title cap is generous on purpose: shelved/delayed releases are real
            # (The Long Farewell shot 1971 released 1987; Un chant d'amour shot 1950,
            # TMDB dates its legal release 1972). Still guards person homonyms.
            continue
        candidates.append((t_score, year_diff, c.get("id")))
    candidates.sort()
    if not candidates:
        return None
    if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2]:
        return None  # tie -> ambiguous
    return candidates[0][2]


def parse_film_list(path: str) -> List[Dict]:
    """Parse a TSPDT-style TSV: Pos, PrevRank, Title, Director, Year, Country, Mins.
    Rows whose first column isn't an integer (headers, blanks) are skipped."""
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 5 or not parts[0].strip().isdigit():
                continue
            raw_title = parts[2].strip()
            year_match = re.search(r"\d{4}", parts[4])
            if not raw_title or not year_match:
                continue
            imdb_match = re.search(r"tt\d+", parts[7]) if len(parts) > 7 else None
            rows.append({
                "pos": int(parts[0]),
                "title": deinvert_article(fix_mojibake(raw_title).replace("[TV]", "").strip()),
                "director": fix_mojibake(parts[3].strip()),
                "year": int(year_match.group()),
                "is_tv": "[TV]" in raw_title,
                "imdb_id": imdb_match.group() if imdb_match else None,
            })
    return rows


class DatabaseSeeder:
    def __init__(
        self,
        limit: int = 15000,
        strategy: str = "popular",
        language: Optional[str] = None,
        company_id: Optional[int] = None,
        collection_id: Optional[int] = None,
        file: Optional[str] = None,
        dry_run: bool = False,
    ):
        self.limit = limit
        self.strategy = strategy
        self.language = language
        self.company_id = company_id
        self.collection_id = collection_id
        self.file = file
        self.dry_run = dry_run
        self.tmdb = TMDBClient()
        self.qdrant = QdrantService()
        self.embedding_service = EmbeddingService()
        self.omdb = OMDbClient()
        self.trakt = TraktClient()
        self.factory = MovieFactory(self.tmdb, self.omdb, self.embedding_service)
        self.processed_count = 0
        self.error_count = 0
        
    async def get_existing_tmdb_ids(self, db) -> set:
        """Fetch all TMDB IDs currently in the database"""
        result = await db.execute(select(Movie.tmdb_id))
        return set(result.scalars().all())

    async def fetch_top_movies(self, existing_ids: set) -> List[Dict]:
        """
        Fetch top rated movies from TMDB until we find enough NEW movies to meet the limit.
        """
        candidates = []
        page = 1
        max_pages = 500 # Safety limit
        new_found = 0
        
        pbar = tqdm(total=self.limit, desc="Finding NEW movies")
        
        while new_found < self.limit and page <= max_pages:
            try:
                # Fetch a page of top rated movies
                results = await self.tmdb.discover_movies(
                    sort_by="vote_count.desc", # Popular/Well-known first
                    vote_count_min=50,
                    page=page
                )
                
                if not results:
                    break
                    
                for movie in results:
                    if movie["id"] not in existing_ids:
                        candidates.append(movie)
                        existing_ids.add(movie["id"]) # Prevent duplicates in same run
                        new_found += 1
                        pbar.update(1)
                        
                        if new_found >= self.limit:
                            break
                
                page += 1
                
            except Exception as e:
                logger.error(f"Error fetching page {page}: {e}")
                break
                
        pbar.close()
        return candidates

    # Old process_movie method removed in favor of prepare_movie_batch_item

    async def _discover_loop(
        self,
        existing_ids: set,
        discover_kwargs: Dict,
        desc: str,
        max_pages: int = 500,
    ) -> List[Dict]:
        """
        Generic discover-paginate loop. Pages through TMDB /discover with the given kwargs
        until `self.limit` new (non-duplicate) movies are found or max_pages is reached.
        """
        candidates = []
        page = 1
        pbar = tqdm(total=self.limit, desc=desc)
        while len(candidates) < self.limit and page <= max_pages:
            try:
                results = await self.tmdb.discover_movies(page=page, **discover_kwargs)
                if not results:
                    break
                for movie in results:
                    if movie["id"] not in existing_ids:
                        candidates.append(movie)
                        existing_ids.add(movie["id"])
                        pbar.update(1)
                        if len(candidates) >= self.limit:
                            break
                page += 1
            except Exception as e:
                logger.error(f"Error fetching {desc} page {page}: {e}")
                break
        pbar.close()
        return candidates

    async def fetch_top_rated_movies(self, existing_ids: set) -> List[Dict]:
        """Critics' favorites: vote_average.desc with a high vote_count floor.

        Floor lowered 1500 → 1000 (2026-07-03): the ≥1500 tier was fully
        absorbed into the catalog (a 100-film run yielded 0 new), so 1000
        opens the next tier while staying well above sparse-vote territory.
        """
        return await self._discover_loop(
            existing_ids,
            {"sort_by": "vote_average.desc", "vote_count_min": 1000},
            "Finding NEW top-rated movies",
        )

    async def fetch_by_language_movies(self, existing_ids: set) -> List[Dict]:
        """Non-English-language cinema: with_original_language + vote_count.desc."""
        if not self.language:
            logger.error("by_language strategy requires --language <iso-639-1>")
            return []
        return await self._discover_loop(
            existing_ids,
            {
                "sort_by": "vote_count.desc",
                "vote_count_min": 30,
                "with_original_language": self.language,
            },
            f"Finding NEW {self.language}-language movies",
        )

    async def fetch_classic_movies(self, existing_ids: set) -> List[Dict]:
        """Pre-1990 cinema: sort by vote_count, capped release date."""
        return await self._discover_loop(
            existing_ids,
            {
                "sort_by": "vote_count.desc",
                "vote_count_min": 100,
                "primary_release_date_lte": "1990-12-31",
            },
            "Finding NEW classic movies",
        )

    async def fetch_trending_movies(self, existing_ids: set) -> List[Dict]:
        """What's hot this week. Uses /trending/movie/week (capped ~60-1000 by TMDB)."""
        candidates = []
        page = 1
        pbar = tqdm(total=self.limit, desc="Finding NEW trending movies")
        while len(candidates) < self.limit and page <= 50:
            data = await self.tmdb._make_request(f"/trending/movie/week", {"page": page})
            results = (data or {}).get("results", [])
            if not results:
                break
            for movie in results:
                if movie["id"] not in existing_ids:
                    candidates.append(movie)
                    existing_ids.add(movie["id"])
                    pbar.update(1)
                    if len(candidates) >= self.limit:
                        break
            page += 1
        pbar.close()
        return candidates

    async def _trakt_loop(
        self,
        existing_ids: set,
        trakt_method,
        desc: str,
        max_pages: int = 100,
    ) -> List[Dict]:
        """Pages through a Trakt list endpoint, extracting tmdb_ids and deduping."""
        if not self.trakt.enabled:
            logger.error("TRAKT_CLIENT_ID not set — cannot use trakt_* strategies")
            return []

        candidates = []
        page = 1
        pbar = tqdm(total=self.limit, desc=desc)
        while len(candidates) < self.limit and page <= max_pages:
            try:
                items = await trakt_method(page=page, limit=100)
                if not items:
                    break
                for movie in items:
                    tmdb_id = movie.get("ids", {}).get("tmdb")
                    if not tmdb_id:
                        continue
                    if tmdb_id in existing_ids:
                        continue
                    candidates.append({"id": tmdb_id})
                    existing_ids.add(tmdb_id)
                    pbar.update(1)
                    if len(candidates) >= self.limit:
                        break
                page += 1
            except Exception as e:
                logger.error(f"Error fetching {desc} page {page}: {e}")
                break
        pbar.close()
        return candidates

    async def fetch_trakt_popular(self, existing_ids: set) -> List[Dict]:
        return await self._trakt_loop(existing_ids, self.trakt.popular, "Finding NEW Trakt-popular movies")

    async def fetch_trakt_trending(self, existing_ids: set) -> List[Dict]:
        return await self._trakt_loop(existing_ids, self.trakt.trending, "Finding NEW Trakt-trending movies")

    async def fetch_trakt_anticipated(self, existing_ids: set) -> List[Dict]:
        return await self._trakt_loop(existing_ids, self.trakt.anticipated, "Finding NEW Trakt-anticipated movies")

    async def fetch_by_company_movies(self, existing_ids: set) -> List[Dict]:
        """All movies from a TMDB production company. Sorted by vote_count.desc
        so the most-known films of the company come first."""
        if not self.company_id:
            logger.error("by_company strategy requires --company-id <N>")
            return []
        return await self._discover_loop(
            existing_ids,
            {
                "sort_by": "vote_count.desc",
                "vote_count_min": 0,
                "with_companies": str(self.company_id),
            },
            f"Finding NEW films by company {self.company_id}",
        )

    async def fetch_by_collection_movies(self, existing_ids: set) -> List[Dict]:
        """Enumerate every film in a TMDB collection (saga). NOT paginated —
        TMDB returns all parts in one response. `self.limit` is ignored here
        because collections are bounded by definition."""
        if not self.collection_id:
            logger.error("by_collection strategy requires --collection-id <N>")
            return []
        data = await self.tmdb.get_collection(self.collection_id)
        if not data:
            logger.error(f"Collection {self.collection_id} not found in TMDB")
            return []
        name = data.get("name", f"#{self.collection_id}")
        parts = data.get("parts") or []
        candidates = []
        for part in parts:
            tmdb_id = part.get("id")
            if not tmdb_id or tmdb_id in existing_ids:
                continue
            candidates.append({"id": tmdb_id, "release_date": part.get("release_date")})
            existing_ids.add(tmdb_id)
        logger.info(f"Collection '{name}': {len(parts)} parts, {len(candidates)} NEW to seed")
        return candidates

    async def _resolve_list_row(self, row: Dict) -> Optional[int]:
        """Resolve a title/director/year row to a tmdb_id, or None if no confident match.
        IMDb id first when the list provides one (TMDB /find — exact, no heuristics);
        else title search (cheap, cached, right ~98% of the time); on failure, fall back
        to the director's filmography (title-agnostic — survives divergent English titles,
        homonym ranking traps, and director-name transliterations)."""
        if row.get("imdb_id"):
            data = await self.tmdb._make_request(
                f"/find/{row['imdb_id']}", {"external_source": "imdb_id"}
            )
            movies = (data or {}).get("movie_results") or []
            if movies:
                return movies[0]["id"]
            # no movie behind that tt-id (TV/episode/dead link) -> heuristic chain
        tmdb_id = await self._resolve_by_title(row)
        if tmdb_id is None:
            tmdb_id = await self._resolve_via_director(row)
            if tmdb_id is not None:
                logger.info(
                    f"Resolved via director filmography: '{row['title']}' ({row['year']}) -> {tmdb_id}"
                )
        return tmdb_id

    async def _resolve_via_director(self, row: Dict) -> Optional[int]:
        """Find the film inside the director's TMDB filmography."""
        name = row["director"].split("&")[0].split("/")[0].strip()
        if "," in name:
            last, _, first = name.partition(",")
            name = f"{first.strip()} {last.strip()}"
        if not name or "various" in name.lower():
            return None
        data = await self.tmdb._make_request("/search/person", {"query": name})
        # Top-3 persons: name homonyms are common (Max vs Marcel Ophüls, two Kim Ki-duks)
        for person in ((data or {}).get("results") or [])[:3]:
            credits = await self.tmdb._make_request(f"/person/{person['id']}/movie_credits", {})
            directed = [c for c in (credits or {}).get("crew") or [] if c.get("job") == "Director"]
            found = pick_from_filmography(directed, row["title"], row["year"])
            if found is not None:
                return found
        return None

    async def _resolve_by_title(self, row: Dict) -> Optional[int]:
        """Title+year search with a director veto against homonym traps."""
        title, year = row["title"], row["year"]
        hit = None
        for yr in (year, year + 1, year - 1, None):
            hit = await self.tmdb.search_movie(title, year=yr)
            if hit:
                break
        if not hit:
            # Mojibake leftovers the round-trip couldn't repair: retry ASCII-only
            clean = re.sub(r"\s+", " ", re.sub(r"[^\x20-\x7E]", " ", title)).strip(" .")
            if clean and clean != title:
                hit = await self.tmdb.search_movie(clean, year=year)
        if not hit:
            return None

        # Year sanity — the no-year pass can return a remake/homonym from any era
        rd = (hit.get("release_date") or "")[:4]
        if rd.isdigit() and abs(int(rd) - year) > 2:
            return None

        # Director gate — catches homonym traps ('Blue' 1993: Jarman vs Kieslowski).
        # get_movie_details is Redis-cached and reused by MovieFactory at ingest,
        # so this verification costs nothing extra on the real run.
        wanted = wanted_director_keys(row["director"])
        if wanted:
            details = await self.tmdb.get_movie_details(hit["id"])
            crew = ((details or {}).get("credits") or {}).get("crew") or []
            directors = [
                re.sub(r"[^a-z]", "", ascii_fold(c.get("name", "")))
                for c in crew if c.get("job") == "Director"
            ]
            if directors and not any(w in d for w in wanted for d in directors):
                logger.info(
                    f"Director mismatch for '{title}' ({year}): wanted ~{wanted}, got {directors}"
                )
                return None
        return hit["id"]

    async def fetch_from_file(self, existing_ids: set) -> List[Dict]:
        """Curated-list ingestion (e.g. TSPDT 1000): resolve title/director/year rows
        to TMDB IDs. No popularity floor — the list IS the curation. Unresolved rows
        go to <file>.unresolved.csv for manual review, never a silent guess."""
        if not self.file:
            logger.error("from_file strategy requires --file <path>")
            return []
        rows = parse_film_list(self.file)
        if not rows:
            logger.error(f"No parseable rows in {self.file}")
            return []

        stats = {"tv_skipped": 0, "already": 0, "unresolved": 0}
        candidates, unresolved = [], []
        target = len(rows) if self.dry_run else self.limit
        pbar = tqdm(total=len(rows), desc="Resolving list rows")
        for row in rows:
            if row["is_tv"]:
                stats["tv_skipped"] += 1
                pbar.update(1)
                continue
            if len(candidates) >= target:
                break
            tmdb_id = await self._resolve_list_row(row)
            if tmdb_id is None:
                stats["unresolved"] += 1
                unresolved.append(row)
            elif tmdb_id in existing_ids:
                stats["already"] += 1
            else:
                candidates.append({"id": tmdb_id})
                existing_ids.add(tmdb_id)
            # the bar tracks rows SCANNED; the postfix tracks what --limit actually caps
            pbar.set_postfix(
                new=f"{len(candidates)}/{target}",
                already=stats["already"],
                unresolved=stats["unresolved"],
                refresh=False,
            )
            pbar.update(1)
        pbar.close()

        out = self.file + ".unresolved.csv"
        if unresolved:
            with open(out, "w", encoding="utf-8") as f:
                f.write("pos\ttitle\tdirector\tyear\n")
                for r in unresolved:
                    f.write(f"{r['pos']}\t{r['title']}\t{r['director']}\t{r['year']}\n")
            logger.info(f"Unresolved rows written to {out}")
        elif os.path.exists(out):
            os.remove(out)  # stale CSV from a previous run

        logger.info(
            f"List report: {len(rows)} rows | {stats['tv_skipped']} TV skipped | "
            f"{stats['already']} already in catalogue | {len(candidates)} NEW | "
            f"{stats['unresolved']} unresolved"
        )
        return candidates

    async def fetch_upcoming_movies(self, existing_ids: set) -> list:
        """Fetch upcoming movies releasing in next 6 months."""
        from datetime import date, timedelta
        today = date.today().isoformat()
        future = (date.today() + timedelta(days=180)).isoformat()

        candidates = []
        page = 1

        pbar = tqdm(total=self.limit, desc="Finding NEW upcoming movies")
        while len(candidates) < self.limit and page <= 50:
            try:
                results = await self.tmdb.discover_movies(
                    sort_by="popularity.desc",
                    primary_release_date_gte=today,
                    primary_release_date_lte=future,
                    vote_count_min=None,  # upcoming films have 0 votes — no filter
                    page=page,
                )
                for movie in (results or []):
                    if movie["id"] not in existing_ids:
                        if movie.get("popularity", 0) >= 5.0:
                            candidates.append(movie)
                            existing_ids.add(movie["id"])
                            pbar.update(1)
                            if len(candidates) >= self.limit:
                                break
                page += 1
            except Exception as e:
                logger.error(f"Error fetching upcoming page {page}: {e}")
                break
        pbar.close()
        return candidates[:self.limit]

    async def fetch_recent_movies(self, existing_ids: set) -> list:
        """Estrenos de los últimos 90 días, por DOS caminos que no se solapan.

        1. Mundial por `primary_release_date` — lo de siempre, con suelo de 20 votos.
        2. **Estrenos en cine en `RECENT_REGION`** (`region` + `with_release_type=3` +
           `release_date.*`), sin suelo de votos.

        El segundo existe porque el primero es CIEGO a las películas extranjeras que
        llegan tarde: `primary_release_date` es la fecha MUNDIAL, así que cuando una
        película se estrena en cines españoles su fecha primaria puede tener 8-16 meses
        y cae fuera de la ventana. Medido 2026-08-11 — de 18 títulos en cartelera ES que
        faltaban en catálogo:

            Jumbo     primaria 2025-03-31 → cines ES 2026-07-24
            Kangaroo  primaria 2025-08-21 → cines ES 2026-08-12
            Omaha     primaria 2025-11-22 → cines ES 2026-07-17

        El camino 1 devolvía **0 nuevas** y el 2 devuelve **15** sobre la misma ventana
        (La ventana abierta, Tres de más, Wham! 10 Days in...). Y sin suelo de votos a
        propósito: un estreno de esta semana no ha tenido tiempo de acumular 20, que es
        justo lo que tumbaba a 14 de esas 18. El filtro de calidad aquí es haber llegado
        a una sala, no el recuento de votos.
        """
        from datetime import date, timedelta
        today = date.today().isoformat()
        past_90 = (date.today() - timedelta(days=90)).isoformat()

        pasadas = [
            dict(sort_by="primary_release_date.desc", primary_release_date_gte=past_90,
                 primary_release_date_lte=today, vote_count_min=20),
            dict(sort_by="primary_release_date.desc", region=RECENT_REGION,
                 with_release_type="3", release_date_gte=past_90,
                 release_date_lte=today, vote_count_min=0),
        ]

        candidates = []
        pbar = tqdm(total=self.limit, desc="Finding NEW recent movies")
        for kwargs in pasadas:
            page = 1
            while len(candidates) < self.limit and page <= 50:
                try:
                    results = await self.tmdb.discover_movies(page=page, **kwargs)
                    if not results:
                        break
                    for movie in results:
                        if movie["id"] not in existing_ids:
                            candidates.append(movie)
                            existing_ids.add(movie["id"])
                            pbar.update(1)
                            if len(candidates) >= self.limit:
                                break
                    page += 1
                except Exception as e:
                    logger.error(f"Error fetching recent page {page}: {e}")
                    break
        pbar.close()
        return candidates[:self.limit]

    async def fetch_for_current_strategy(self, existing_ids: set) -> List[Dict]:
        """Dispatch to the right fetcher based on `self.strategy`."""
        if self.strategy == "upcoming":
            return await self.fetch_upcoming_movies(existing_ids)
        if self.strategy == "recent":
            return await self.fetch_recent_movies(existing_ids)
        if self.strategy == "top_rated":
            return await self.fetch_top_rated_movies(existing_ids)
        if self.strategy == "by_language":
            return await self.fetch_by_language_movies(existing_ids)
        if self.strategy == "classic":
            return await self.fetch_classic_movies(existing_ids)
        if self.strategy == "trending":
            return await self.fetch_trending_movies(existing_ids)
        if self.strategy == "trakt_popular":
            return await self.fetch_trakt_popular(existing_ids)
        if self.strategy == "trakt_trending":
            return await self.fetch_trakt_trending(existing_ids)
        if self.strategy == "trakt_anticipated":
            return await self.fetch_trakt_anticipated(existing_ids)
        if self.strategy == "by_company":
            return await self.fetch_by_company_movies(existing_ids)
        if self.strategy == "by_collection":
            return await self.fetch_by_collection_movies(existing_ids)
        if self.strategy == "from_file":
            return await self.fetch_from_file(existing_ids)
        return await self.fetch_top_movies(existing_ids)

    async def seed_batch(self, db, existing_ids: set):
        """
        Run ONE seed pass with the current strategy state. Persists to DB+Qdrant
        and mutates `existing_ids` so callers can chain multiple batches without
        re-querying the DB. Does NOT close any clients.
        """
        logger.info(f"Seeding batch (Strategy: {self.strategy}, Limit: {self.limit})")
        new_movies = await self.fetch_for_current_strategy(existing_ids)
        logger.info(f"Fetched {len(new_movies)} NEW movies to process")

        if self.dry_run:
            logger.info(f"DRY RUN — {len(new_movies)} new movies WOULD be ingested; nothing written")
            return

        if not new_movies:
            return

        pbar = tqdm(total=len(new_movies), desc="Seeding Movies (Batch Mode)")
        chunk_size = 50
        for i in range(0, len(new_movies), chunk_size):
            chunk = new_movies[i:i+chunk_size]
            movies_batch = []
            points_batch = []
            for movie_data in chunk:
                try:
                    result = await self.prepare_movie_batch_item(movie_data, db)
                    if result:
                        movie, point = result
                        movies_batch.append(movie)
                        points_batch.append(point)
                except Exception as e:
                    logger.error(f"Error preparing movie {movie_data.get('id')}: {e}")
                    self.error_count += 1
                finally:
                    pbar.update(1)

            if movies_batch:
                try:
                    db.add_all(movies_batch)
                    await db.commit()
                    if points_batch:
                        await self.qdrant.upsert_batch(points_batch)
                        self.processed_count += len(movies_batch)
                except Exception as e:
                    logger.error(f"Batch commit failed: {e}")
                    await db.rollback()
                    self.error_count += len(movies_batch)
        pbar.close()

    async def aclose(self):
        """Close all external clients. Call once after all seed_batch() calls."""
        await self.tmdb.aclose()
        await self.omdb.close()
        await self.trakt.aclose()

    async def run(self):
        """One-shot entry: init, seed one batch with current strategy, close."""
        logger.info(f"Starting database seed (Limit: {self.limit}, Strategy: {self.strategy})")
        await self.qdrant.init_collection()
        try:
            async with AsyncSessionLocal() as db:
                existing_ids = await self.get_existing_tmdb_ids(db)
                logger.info(f"Found {len(existing_ids)} existing movies in DB")
                await self.seed_batch(db, existing_ids)
            logger.info(
                f"Seeding complete. Processed: {self.processed_count}, "
                f"Errors: {self.error_count}"
            )
        finally:
            await self.aclose()

    async def prepare_movie_batch_item(self, movie_data: Dict, db):
        """
        Prepares a single movie for batch insertion using the unified MovieFactory.
        Returns (MovieObject, PointStruct)
        """
        tmdb_id = movie_data["id"]

        # Delegate to factory
        # Factory returns (Movie, Point, ProvidersData)
        movie, point, _ = await self.factory.build_movie(tmdb_id)

        if not movie:
            return None

        if self.strategy in ("upcoming", "trakt_anticipated"):
            from datetime import date
            release_dates = await self.tmdb.get_release_dates(tmdb_id)
            us_str = release_dates.get("us")
            es_str = release_dates.get("es")
            movie.release_date_us = date.fromisoformat(us_str) if us_str else None
            movie.release_date_es = date.fromisoformat(es_str) if es_str else None
            # Fallback worldwide: use TMDB release_date field from movie_data
            ww_str = movie_data.get("release_date")
            movie.release_date_ww = date.fromisoformat(ww_str) if ww_str else None
            movie.is_upcoming = True

        return movie, point
                

async def main():
    parser = argparse.ArgumentParser(description="Seed database with TMDB movies")
    parser.add_argument("--limit", type=int, default=15000, help="Number of movies to fetch")
    parser.add_argument(
        "--strategy",
        choices=[
            "popular", "recent", "upcoming", "top_rated", "by_language", "classic", "trending",
            "trakt_popular", "trakt_trending", "trakt_anticipated",
            "by_company", "by_collection", "from_file",
        ],
        default="popular",
        help=(
            "popular: vote_count.desc | recent: last 90 days | upcoming: next 180 days | "
            "top_rated: vote_average.desc with vote_count>=1000 | "
            "by_language: requires --language <iso-639-1> | "
            "classic: pre-1990 by vote_count | trending: /trending/movie/week | "
            "trakt_popular | trakt_trending | trakt_anticipated (require TRAKT_CLIENT_ID) | "
            "by_company: requires --company-id <N> | "
            "by_collection: requires --collection-id <N> (enumerates the whole saga) | "
            "from_file: requires --file <TSV: Pos/Rank/Title/Director/Year/Country/Mins>"
        ),
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="ISO 639-1 code for by_language strategy (e.g. es, ja, ko, fr, de, it)",
    )
    parser.add_argument(
        "--company-id",
        type=int,
        default=None,
        help="TMDB production company ID for by_company (e.g. 420=Marvel Studios, 3=Pixar, 10342=Studio Ghibli)",
    )
    parser.add_argument(
        "--collection-id",
        type=int,
        default=None,
        help="TMDB collection ID for by_collection (e.g. 10=Star Wars, 1241=Harry Potter, 645=James Bond)",
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Path to a curated list TSV for from_file (e.g. scripts/data/tspdt_top1000.tsv)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and report (rows / already-in-catalogue / NEW / unresolved) without ingesting anything",
    )
    args = parser.parse_args()

    seeder = DatabaseSeeder(
        limit=args.limit,
        strategy=args.strategy,
        language=args.language,
        company_id=args.company_id,
        collection_id=args.collection_id,
        file=args.file,
        dry_run=args.dry_run,
    )
    await seeder.run()

if __name__ == "__main__":
    asyncio.run(main())
