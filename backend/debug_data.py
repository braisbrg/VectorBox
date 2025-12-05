
import asyncio
import os
import sys
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Add backend directory to path so we can import models
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from models.database import Base, User, Movie, UserRating

load_dotenv()

# Force asyncpg driver
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:password@localhost/letterboxd_db")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

async def debug_data():
    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # 1. Count Stats
        user_count = await session.scalar(select(func.count(User.id)))
        movie_count = await session.scalar(select(func.count(Movie.id)))
        rating_count = await session.scalar(select(func.count(UserRating.id)))
        
        # print(f"\n=== Database Stats ===")
        # print(f"Users: {user_count}")
        # print(f"Movies: {movie_count}")
        # print(f"User Ratings/Interactions: {rating_count}")

        # 2. Check for Duplicates (by TMDB ID)
        # print(f"\n=== Checking for Duplicates (TMDB ID) ===")
        duplicates = await session.execute(
            select(Movie.tmdb_id, func.count(Movie.id))
            .group_by(Movie.tmdb_id)
            .having(func.count(Movie.id) > 1)
        )
        dupes = duplicates.all()
        if dupes:
            print(f"FOUND {len(dupes)} DUPLICATE TMDB IDs:")
            for tmdb_id, count in dupes:
                print(f"  TMDB ID {tmdb_id}: {count} records")
                # Show details
                records = await session.execute(select(Movie).where(Movie.tmdb_id == tmdb_id))
                for r in records.scalars():
                    print(f"    - ID: {r.id}, Title: {r.title}, Year: {r.year}")
        else:
            # print("No duplicates found by TMDB ID.")
            pass

        # 3. Show Sample User Data
        # print(f"\n=== Sample User Data (Latest 10 Interactions) ===")
        # Get user with most ratings
        user_id = 1 # Default
        
        stmt = (
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .order_by(UserRating.id.desc())
            .limit(10)
        )
        results = await session.execute(stmt)
        
        # print(f"{'Title':<40} | {'Year':<6} | {'Rating':<6} | {'Watchlist':<9} | {'Watched':<10} | {'Date'}")
        # print("-" * 100)
        
        for rating, movie in results:
            r_val = str(rating.rating) if rating.rating else "-"
            wl_val = "YES" if rating.is_watchlist else "-"
            w_val = "YES" if rating.is_watched else "-"
            d_val = str(rating.watched_date) if rating.watched_date else "-"
            
        # print(f"{movie.title[:38]:<40} | {movie.year or '-':<6} | {r_val:<6} | {wl_val:<9} | {w_val:<10} | {d_val}")

        # print(f"\n=== Database Stats (Summary) ===")
        # print(f"Users: {user_count}")
        # print(f"Movies: {movie_count}")
        # print(f"User Ratings/Interactions: {rating_count}")

    # Check Qdrant
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(host="localhost", port=6333)
        collections = client.get_collections()
        print(f"\n=== Qdrant Stats ===")
        for col in collections.collections:
            info = client.get_collection(col.name)
            print(f"Collection: {col.name}, Points: {info.points_count}")
    except Exception as e:
        print(f"\nQdrant Check Failed: {e}")

    await engine.dispose()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(debug_data())
