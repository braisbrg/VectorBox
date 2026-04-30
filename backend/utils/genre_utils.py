"""
Genre utilities — shared genre analysis functions used by both
recommendation_engine.py and recommendation_service.py.

Extracted to avoid circular imports between the engine and service layers.
"""
from typing import Set

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

from models.database import Movie, UserRating

# Generic genres co-occur across most films and don't tell us anything about user taste.
GENERIC_GENRES = {"Action", "Drama", "Comedy", "Adventure", "Thriller"}


async def get_distinctive_user_genres(user_id: int, db: AsyncSession) -> Set[str]:
    """Top genres in user's rated history minus the generic set.

    Returns empty set if user has no rated films (cold start) or if every
    top genre is generic — in both cases the genre coherence filter is skipped.
    """
    result = await db.execute(
        select(
            func.unnest(Movie.genres).label("genre"),
            func.count().label("cnt"),
        )
        .join(UserRating, Movie.id == UserRating.movie_id)
        .where(UserRating.user_id == user_id)
        .where(
            or_(
                UserRating.rating >= 3.5,
                UserRating.is_liked.is_(True),
            )
        )
        .group_by(func.unnest(Movie.genres))
        .order_by(func.count().desc())
        .limit(10)
    )
    top_genres = {row.genre for row in result.all() if row.genre}
    return top_genres - GENERIC_GENRES
