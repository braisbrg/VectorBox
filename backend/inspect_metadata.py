
import asyncio
import os
import sys

sys.path.append(os.getcwd())

from config import AsyncSessionLocal
from models.database import Movie
from sqlalchemy import select

async def inspect():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Movie).where(Movie.title.ilike("%Train Dreams%")))
        movie = result.scalars().first()
        
        if movie:
            print(f"Title: {movie.title}")
            print(f"Overview: '{movie.overview}'")
            print(f"Overview Length: {len(movie.overview) if movie.overview else 0}")
            print(f"Genres: {movie.genres}")
        else:
            print("Movie not found")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(inspect())
