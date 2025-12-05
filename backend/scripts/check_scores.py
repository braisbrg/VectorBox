import asyncio
import sys
import os

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_db
from sqlalchemy import select, func
from models.database import Movie

async def main():
    async for db in get_db():
        try:
            # Count total movies
            total_result = await db.execute(select(func.count(Movie.id)))
            total = total_result.scalar()
            
            # Count movies with vectorbox_score
            score_result = await db.execute(select(func.count(Movie.id)).where(Movie.vectorbox_score.isnot(None)))
            with_score = score_result.scalar()
            
            # Count movies with low score (< 40)
            low_score_result = await db.execute(select(func.count(Movie.id)).where(Movie.vectorbox_score < 40))
            low_score = low_score_result.scalar()
            
            print(f"Total Movies: {total}")
            print(f"With VectorBox Score: {with_score} ({with_score/total*100:.1f}%)")
            print(f"Low Quality (<40): {low_score} ({low_score/total*100:.1f}%)")
            
            # Sample some low quality ones
            if low_score > 0:
                sample = await db.execute(select(Movie.title, Movie.vectorbox_score).where(Movie.vectorbox_score < 40).limit(5))
                print("\nSample Low Quality:")
                for row in sample:
                    print(f"- {row.title}: {row.vectorbox_score}")
                    
        finally:
            await db.close()

if __name__ == "__main__":
    asyncio.run(main())
