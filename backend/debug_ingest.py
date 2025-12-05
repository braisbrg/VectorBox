import asyncio
import logging
import sys
from database import init_db
from config import AsyncSessionLocal
from services.movie_service import MovieService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def debug_ingest(tmdb_id):
    print(f"--- Debugging Ingestion for TMDB ID: {tmdb_id} ---")
    
    await init_db()
    
    async with AsyncSessionLocal() as db:
        movie_service = MovieService(db)
        
        # Try to get or create
        try:
            print("Calling get_or_create_movie...")
            movie = await movie_service.get_or_create_movie(tmdb_id=tmdb_id)
            
            if movie:
                print(f"SUCCESS! Movie: {movie.title} (ID: {movie.id})")
                print(f"VectorBox Score: {movie.vectorbox_score}")
            else:
                print("FAILURE! Movie returned None.")
                
        except Exception as e:
            print(f"EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await movie_service.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_ingest.py <tmdb_id>")
    else:
        asyncio.run(debug_ingest(int(sys.argv[1])))
