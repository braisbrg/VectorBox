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
    def __init__(self, limit: int = 15000, strategy: str = "popular"):
        self.limit = limit
        self.strategy = strategy
        self.tmdb = TMDBClient()
        self.qdrant = QdrantService()
        self.embedding_service = EmbeddingService()
        self.omdb = OMDbClient()
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

    async def run(self):
        logger.info(f"Starting database seed (Limit: {self.limit}, Strategy: {self.strategy})")

        # Initialize Qdrant collection
        await self.qdrant.init_collection()

        async with AsyncSessionLocal() as db:
            # 1. Get existing IDs
            existing_ids = await self.get_existing_tmdb_ids(db)
            logger.info(f"Found {len(existing_ids)} existing movies in DB")

            # 2. Fetch candidates based on strategy
            if self.strategy == "upcoming":
                new_movies = await self.fetch_upcoming_movies(existing_ids)
            elif self.strategy == "recent":
                new_movies = await self.fetch_recent_movies(existing_ids)
            else:
                new_movies = await self.fetch_top_movies(existing_ids)
            logger.info(f"Fetched {len(new_movies)} NEW movies to process")
            
            # 3. Filter (Redundant step removed)
            
            if not new_movies:
                logger.info("No new movies to add.")
                return

            # 4. Process
            pbar = tqdm(total=len(new_movies), desc="Seeding Movies (Batch Mode)")
            
            # Process in chunks to commit to DB periodically
            chunk_size = 50
            for i in range(0, len(new_movies), chunk_size):
                chunk = new_movies[i:i+chunk_size]
                
                movies_batch = []
                points_batch = []
                
                # A. Prepare Batch
                for movie_data in chunk:
                    try:
                        # Prepare data (fetch + embed + object creation)
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
                
                # B. Bulk Persist (SQL)
                if movies_batch:
                    try:
                        db.add_all(movies_batch)
                        await db.commit()
                        
                        # C. Bulk Persist (Vector DB)
                        # Only upsert vectors if SQL commit succeeded
                        if points_batch:
                             # Important: Map DB IDs to Qdrant Points? 
                             # Currently using TMDB ID as ID in Qdrant (seed_db.py line 213 in original: movie_id=movie.id)
                             # Original code used movie.id (Auto-increment PK).
                             # However, we prefer using TMDB ID or UUID for consistency?
                             # Looking at `process_movie` original: movie_id=movie.id.
                             # BUT `ingest_movie` uses `movie.tmdb_id` in line 111 of movie_service.py?
                             # Wait, let's check movie_service.py again. 
                             # seed_db.py L212: movie_id=movie.id
                             # movie_service.py L111: movie_id=movie.tmdb_id
                             # This is an INCONSISTENCY I should fix. 
                             # Best practice: Use TMDB ID for Qdrant ID if it's integer, simpler to lookup.
                             # If I change this now, I might break existing specific lookups if they rely on PK.
                             # But `ingest_movie` uses `tmdb_id`. So I should align `seed_db` to use `tmdb_id`.
                             # Wait, I CANNOT get `movie.id` (PK) before commit if I use `add_all` on async driver sometimes?
                             # Actually `add_all` + `commit` generates IDs.
                             # Let's align on TMDB ID for Qdrant ID. It allows upserting before knowing the SQL PK.
                             
                             await self.qdrant.upsert_batch(points_batch)
                             self.processed_count += len(movies_batch)
                             
                    except Exception as e:
                        logger.error(f"Batch commit failed: {e}")
                        await db.rollback()
                        self.error_count += len(movies_batch)

            pbar.close()
            
        logger.info(f"Seeding complete. Processed: {self.processed_count}, Skipped: {self.skipped_count}, Errors: {self.error_count}")
        
        # Close connections
        await self.tmdb.aclose()
        await self.omdb.close()

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

        if self.strategy == "upcoming":
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
        choices=["popular", "recent", "upcoming"],
        default="popular",
        help="popular: vote_count.desc | recent: last 90 days | upcoming: next 180 days",
    )
    args = parser.parse_args()

    seeder = DatabaseSeeder(limit=args.limit, strategy=args.strategy)
    await seeder.run()

if __name__ == "__main__":
    asyncio.run(main())
