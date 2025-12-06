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
    def __init__(self, limit: int = 15000):
        self.limit = limit
        self.tmdb = TMDBClient()
        self.qdrant = QdrantService()
        self.embedding_service = EmbeddingService()
        self.omdb = OMDbClient()
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

    async def process_movie(self, movie_data: Dict, db):
        """Process a single movie: fetch details, embed, save"""
        try:
            tmdb_id = movie_data["id"]
            
            # ... (details fetching remains same)
            details = await self.tmdb.get_movie_details(tmdb_id)
            if not details:
                logger.warning(f"Could not fetch details for {tmdb_id}")
                return

            title = details.get("title")
            overview = details.get("overview")
            year = int(details.get("release_date", "0000").split("-")[0]) if details.get("release_date") else None
            runtime = details.get("runtime")
            poster_path = details.get("poster_path")
            backdrop_path = details.get("backdrop_path")
            genres = [g["name"] for g in details.get("genres", [])]
            vote_average = details.get("vote_average")
            vote_count = details.get("vote_count")
            popularity = details.get("popularity")
            original_language = details.get("original_language")
            
            # Phase 12: Calculate VectorBox Score
            imdb_id = details.get("imdb_id")
            # Optimized: Keywords already fetched in get_movie_details via append_to_response
            keywords = details.get("keywords_flat", [])
            if not keywords:
                 # Fallback if tmdb client didn't extract them (redundancy)
                 keywords = await self.tmdb.get_movie_keywords(tmdb_id)
            
            # Fetch Spanish metadata
            title_es = details.get("title_es")
            overview_es = details.get("overview_es")
            vectorbox_score = None
            imdb_rating = None
            metacritic_rating = None
            rotten_tomatoes_rating = None
            
            if imdb_id and self.omdb.api_key:
                # Check rate limit
                if self.omdb_requests < self.OMDB_LIMIT:
                    try:
                        omdb_data = await self.omdb.fetch_movie_data(imdb_id)
                        if omdb_data:
                            self.omdb_requests += 1
                            vb_data = self.omdb.calculate_vectorbox_score(omdb_data, vote_average)
                            vectorbox_score = vb_data["score"]
                            imdb_rating = vb_data["breakdown"]["imdb"]
                            metacritic_rating = vb_data["breakdown"]["meta"]
                            rotten_tomatoes_rating = vb_data["breakdown"]["rt"]
                    except Exception as e:
                        logger.warning(f"Failed to fetch OMDb data for {title}: {e}")
                else:
                    if self.omdb_requests == self.OMDB_LIMIT:
                        logger.warning("OMDb limit reached (950). Skipping further score fetches.")
                        self.omdb_requests += 1 # Increment once to stop logging this message

            # Create DB object
            movie = Movie(
                tmdb_id=tmdb_id,
                title=title,
                original_title=details.get("original_title"),
                overview=overview,
                year=year,
                runtime=runtime,
                poster_path=poster_path,
                backdrop_path=backdrop_path,
                genres=genres,
                vote_average=vote_average,
                vote_count=vote_count,
                popularity=popularity,
                original_language=original_language,
                keywords=keywords,
                letterboxd_uri=f"https://letterboxd.com/tmdb/{tmdb_id}", # Approximation
                # New Fields
                imdb_id=imdb_id,
                title_es=title_es,
                overview_es=overview_es,
                vectorbox_score=vectorbox_score,
                imdb_rating=imdb_rating,
                metacritic_rating=metacritic_rating,
                rotten_tomatoes_rating=rotten_tomatoes_rating
            )
            
            db.add(movie)
            await db.flush() # Get ID
            
            # Phase 14: Fetch Streaming Providers
            try:
                provider_service = ProviderService(db, self.tmdb)
                await provider_service.get_providers(movie.id, "ES")
            except Exception as e:
                logger.warning(f"Failed to fetch providers for {title}: {e}")

            # Generate embedding
            embedding_data = {
                "title": title,
                "overview": overview,
                "genres": genres,
                "keywords": keywords
            }
            
            embedding = self.embedding_service.generate_embedding(embedding_data)
            
            # Upsert to Qdrant
            await self.qdrant.upsert_movie_vector(
                movie_id=movie.id,
                vector=embedding.tolist(),
                metadata={
                    "tmdb_id": tmdb_id,
                    "title": title,
                    "year": year,
                    "genres": genres,
                    "overview": overview,
                    "poster_path": poster_path,
                    "vote_average": vote_average,
                    "vote_count": vote_count,
                    "runtime": runtime,
                    "original_language": original_language,
                    "keywords": keywords,
                    "vectorbox_score": vectorbox_score
                }
            )
            
            self.processed_count += 1
            
        except Exception as e:
            logger.error(f"Error processing movie {movie_data.get('id')}: {e}")
            self.error_count += 1

    async def run(self):
        logger.info(f"Starting database seed (Limit: {self.limit})")
        
        # Initialize Qdrant collection
        await self.qdrant.init_collection()
        
        async with AsyncSessionLocal() as db:
            # 1. Get existing IDs
            existing_ids = await self.get_existing_tmdb_ids(db)
            logger.info(f"Found {len(existing_ids)} existing movies in DB")
            
            # 2. Fetch candidates (Smart Pagination)
            # candidates already contains only NEW movies because fetch_top_movies filters them
            new_movies = await self.fetch_top_movies(existing_ids)
            logger.info(f"Fetched {len(new_movies)} NEW movies to process")
            
            # 3. Filter (Redundant step removed)
            
            if not new_movies:
                logger.info("No new movies to add.")
                return

            # 4. Process
            pbar = tqdm(total=len(new_movies), desc="Seeding Movies")
            
            # Process in chunks to commit to DB periodically
            chunk_size = 50
            for i in range(0, len(new_movies), chunk_size):
                chunk = new_movies[i:i+chunk_size]
                
                for movie_data in chunk:
                    await self.process_movie(movie_data, db)
                    pbar.update(1)
                    
                await db.commit()
                
            pbar.close()
            
        logger.info(f"Seeding complete. Processed: {self.processed_count}, Skipped: {self.skipped_count}, Errors: {self.error_count}")
        
        # Close connections
        await self.tmdb.close()

async def main():
    parser = argparse.ArgumentParser(description="Seed database with TMDB movies")
    parser.add_argument("--limit", type=int, default=15000, help="Number of movies to fetch")
    args = parser.parse_args()
    
    seeder = DatabaseSeeder(limit=args.limit)
    await seeder.run()

if __name__ == "__main__":
    asyncio.run(main())
