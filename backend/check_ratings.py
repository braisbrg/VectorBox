
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

async def check_ratings():
    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with async_session() as session:
        # Get the first user (assuming it's the active one)
        result = await session.execute(select(User))
        user = result.scalars().first()
        if not user:
            print("No user found.")
            return

        print(f"Checking ratings for user: {user.username} (ID: {user.id})")

        import json
        
        print("\nJSON_OUTPUT_START")
        
        data = {"created_at_sort": [], "watched_date_sort": []}
        
        # Fetch top 10 ratings by created_at (Import Order)
        result = await session.execute(
            select(UserRating, Movie.title)
            .join(Movie)
            .where(UserRating.user_id == user.id, UserRating.rating >= 4.0)
            .order_by(desc(UserRating.created_at))
            .limit(10)
        )
        for rating, title in result:
             data["created_at_sort"].append({
                 "title": title,
                 "rating": rating.rating,
                 "watched_date": str(rating.watched_date) if rating.watched_date else None,
                 "created_at": str(rating.created_at)
             })

        # Fetch top 10 by Watched Date (Diary Order)
        result = await session.execute(
            select(UserRating, Movie.title)
            .join(Movie)
            .where(UserRating.user_id == user.id, UserRating.rating >= 4.0)
            .order_by(desc(func.coalesce(UserRating.watched_date, UserRating.created_at)))
            .limit(10)
        )
        for rating, title in result:
             data["watched_date_sort"].append({
                 "title": title,
                 "rating": rating.rating,
                 "watched_date": str(rating.watched_date) if rating.watched_date else None,
                 "created_at": str(rating.created_at)
             })
             
        print(json.dumps(data))
        print("JSON_OUTPUT_END")

if __name__ == "__main__":
    asyncio.run(check_ratings())
