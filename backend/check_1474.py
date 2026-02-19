
import asyncio
import os
import sys
from sqlalchemy import select

# Fix paths for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

from config import AsyncSessionLocal
from models.database import Movie

async def check_movie_1474():
    async with AsyncSessionLocal() as db:
        stmt = select(Movie).where(Movie.tmdb_id == 1474)
        result = await db.execute(stmt)
        movie = result.scalar_one_or_none()
        
        if movie:
            print(f"ID: {movie.id}")
            print(f"TMDB ID: {movie.tmdb_id}")
            print(f"Title: {movie.title}")
            print(f"Year: {movie.year}")
            print(f"Created At: {movie.created_at}")
        else:
            print("Movie with TMDB ID 1474 not found in local DB.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(check_movie_1474())
