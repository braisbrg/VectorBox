import asyncio
import os
import sys
import logging
import argparse
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

# Suppress other loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

class DatabaseSeeder:
    def __init__(
        self,
        limit: int = 15000,
        strategy: str = "popular",
        language: Optional[str] = None,
        company_id: Optional[int] = None,
        collection_id: Optional[int] = None,
    ):
        self.limit = limit
        self.strategy = strategy
        self.language = language
        self.company_id = company_id
        self.collection_id = collection_id
        self.tmdb = TMDBClient()
        self.qdrant = QdrantService()
        self.embedding_service = EmbeddingService()
        self.omdb = OMDbClient()
        self.trakt = TraktClient()
        self.factory = MovieFactory(self.tmdb, self.omdb, self.embedding_service)
        self.processed_count = 0
        self.skipped_count = 0
        self.error_count = 0
        self.omdb_requests = 0
        self.OMDB_LIMIT = 950 # Daily limit buffer (1000 max)
        
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
        """Critics' favorites: vote_average.desc with a high vote_count floor."""
        return await self._discover_loop(
            existing_ids,
            {"sort_by": "vote_average.desc", "vote_count_min": 1500},
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
        """Fetch movies released in the last 90 days."""
        from datetime import date, timedelta
        today = date.today().isoformat()
        past_90 = (date.today() - timedelta(days=90)).isoformat()

        candidates = []
        page = 1

        pbar = tqdm(total=self.limit, desc="Finding NEW recent movies")
        while len(candidates) < self.limit and page <= 50:
            try:
                results = await self.tmdb.discover_movies(
                    sort_by="primary_release_date.desc",
                    primary_release_date_gte=past_90,
                    primary_release_date_lte=today,
                    vote_count_min=20,
                    page=page,
                )
                for movie in (results or []):
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
                f"Skipped: {self.skipped_count}, Errors: {self.error_count}"
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
            "by_company", "by_collection",
        ],
        default="popular",
        help=(
            "popular: vote_count.desc | recent: last 90 days | upcoming: next 180 days | "
            "top_rated: vote_average.desc with vote_count>=1500 | "
            "by_language: requires --language <iso-639-1> | "
            "classic: pre-1990 by vote_count | trending: /trending/movie/week | "
            "trakt_popular | trakt_trending | trakt_anticipated (require TRAKT_CLIENT_ID) | "
            "by_company: requires --company-id <N> | "
            "by_collection: requires --collection-id <N> (enumerates the whole saga)"
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
    args = parser.parse_args()

    seeder = DatabaseSeeder(
        limit=args.limit,
        strategy=args.strategy,
        language=args.language,
        company_id=args.company_id,
        collection_id=args.collection_id,
    )
    await seeder.run()

if __name__ == "__main__":
    asyncio.run(main())
