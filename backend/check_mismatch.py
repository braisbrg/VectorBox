
import asyncio
import os
import sys
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Add backend to path
sys.path.append(os.path.join(os.getcwd()))

from config import DATABASE_URL
from models.database import UserRating, Movie, User
from services.qdrant_service import QdrantService
from services.movie_service import MovieService

async def debug_feed():
    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    qdrant = QdrantService()

    async with async_session() as session:
        # Get User
        result = await session.execute(select(User))
        user = result.scalars().first()
        print(f"User: {user.username}")

        # 1. Fetch Candidates (Same logic as feed_service)
        print("\n--- Feed Service Candidates Logic ---")
        stmt = (
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(
                UserRating.user_id == user.id,
                UserRating.rating >= 4.0
            )
            .order_by(
                desc(func.coalesce(UserRating.watched_date, UserRating.created_at))
            )
            .limit(2)
        )
        result = await session.execute(stmt)
        candidates = result.all()
        
        for i, (rating, movie) in enumerate(candidates):
            print(f"\nCandidate #{i+1}: {movie.title} (TMDB ID: {movie.tmdb_id})")
            print(f"  - Rating: {rating.rating}")
            print(f"  - Watched: {rating.watched_date}")
            print(f"  - Created: {rating.created_at}")
            
            # Check Vector
            vector = await qdrant.get_vector(movie.tmdb_id)
            has_vector = vector is not None
            print(f"  - Has Vector in Qdrant? {has_vector}")
            
            if not has_vector:
                print("  - [WARNING] MISSING VECTOR")
                if i == 0:
                    print("  - This is the FIRST candidate. Repair should trigger.")
            else:
                 # Try a dummy search
                 results = await qdrant.search_similar(query_vector=vector, limit=5)
                 print(f"  - Search yielded {len(results)} results")

if __name__ == "__main__":
    asyncio.run(debug_feed())
