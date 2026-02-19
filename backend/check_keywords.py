
import asyncio
import os
import sys
from sqlalchemy import select, func, or_

# Fix paths for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

from config import AsyncSessionLocal
from models.database import Movie

async def check_keywords():
    async with AsyncSessionLocal() as db:
        # Total movies
        result = await db.execute(select(func.count(Movie.id)))
        total = result.scalar()
        
        # Movies with keywords
        # Check for non-null and non-empty array
        # Note: In Python, empty array is []
        # In SQL, we check cardinality or simply that it's not null/empty
        
        # Method 1: Fetch all and count (slower but accurate for array types logic)
        stmt = select(Movie.id, Movie.title, Movie.keywords).limit(2000)
        result = await db.execute(stmt)
        movies = result.all()
        
        with_keywords = 0
        without_keywords = 0
        samples_with = []
        samples_without = []
        
        for m in movies:
            if m.keywords and len(m.keywords) > 0:
                with_keywords += 1
                if len(samples_with) < 3:
                     samples_with.append(f"{m.title}: {m.keywords[:5]}...")
            else:
                without_keywords += 1
                if len(samples_without) < 3:
                     samples_without.append(f"{m.title}")
        
        print(f"Total Movies Checked: {len(movies)}")
        print(f"With Keywords: {with_keywords}")
        print(f"Without Keywords: {without_keywords}")
        print("-" * 30)
        print("Sample WITH Keywords:")
        for s in samples_with:
            print(f"  - {s}")
        print("-" * 30)
        print("Sample WITHOUT Keywords:")
        for s in samples_without:
            print(f"  - {s}")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(check_keywords())
