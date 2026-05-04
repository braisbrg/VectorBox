"""
Test the quality of guest feed recommendations.

Usage:
    python scripts/test_guest_feed.py --ratings '{"129": "positive", "155": "positive", "680": "negative"}'
    python scripts/test_guest_feed.py --user-id 212  # use ratings from DB user
    python scripts/test_guest_feed.py --preset cinephile  # use preset profile

Output:
    - List of recommended movies with scores
    - Genre distribution of recommendations
    - Average vectorbox_score
    - Coverage (how many genres from positive seeds appear in recs)
"""

import asyncio
import argparse
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from collections import Counter
from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie, UserRating
from services.qdrant_service import QdrantService

PRESETS = {
    "cinephile": {
        # Arthouse/drama profile
        "965150": "positive",   # Aftersun
        "129":    "positive",   # Spirited Away
        "155":    "positive",   # The Dark Knight
        "680":    "negative",   # Pulp Fiction (anti-vector test)
    },
    "blockbuster": {
        "155":    "positive",   # The Dark Knight
        "27205":  "positive",   # Inception
        "299536": "positive",   # Infinity War
        "680":    "negative",   # Pulp Fiction
    },
}


async def get_ratings_from_user(user_id: int) -> dict:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserRating, Movie)
            .join(Movie, UserRating.movie_id == Movie.id)
            .where(UserRating.user_id == user_id)
            .where(UserRating.rating.isnot(None))
        )
        rows = result.all()
        ratings = {}
        for rating, movie in rows:
            if rating.rating >= 4.0:
                ratings[str(movie.tmdb_id)] = "positive"
            elif rating.rating <= 2.5:
                ratings[str(movie.tmdb_id)] = "negative"
            else:
                ratings[str(movie.tmdb_id)] = "neutral"
        return ratings


async def evaluate_feed(ratings: dict) -> None:
    from routers.public import _compute_guest_feed

    # Convert string keys to int for the endpoint signature
    int_ratings = {int(k): v for k, v in ratings.items()}

    async with AsyncSessionLocal() as db:
        qdrant = QdrantService()
        movies = await _compute_guest_feed(int_ratings, db, qdrant)

    if not movies:
        print("No recommendations returned.")
        return

    print(f"\n=== GUEST FEED QUALITY REPORT ===")
    positive = sum(1 for v in ratings.values() if v == "positive")
    negative = sum(1 for v in ratings.values() if v == "negative")
    neutral  = sum(1 for v in ratings.values() if v == "neutral")
    print(f"Input ratings : {len(ratings)} movies "
          f"({positive} positive, {negative} negative, {neutral} neutral)")
    print(f"Recommendations: {len(movies)} movies\n")

    # Score distribution
    scores = [m.get("vectorbox_score") for m in movies if m.get("vectorbox_score")]
    if scores:
        print(f"VectorBox Score: avg={np.mean(scores):.1f}  "
              f"min={min(scores):.1f}  max={max(scores):.1f}")

    # Genre distribution
    all_genres = [g for m in movies for g in (m.get("genres") or [])]
    genre_counts = Counter(all_genres).most_common(8)
    print(f"\nGenre distribution:")
    for genre, count in genre_counts:
        bar = "█" * count
        print(f"  {genre:<22} {bar} ({count})")

    # Top recommendations
    print(f"\nTop recommendations:")
    for i, m in enumerate(movies[:10], 1):
        genres = "/".join((m.get("genres") or [])[:2])
        score  = m.get("vectorbox_score", "?")
        score_str = f"{score:.1f}" if isinstance(score, float) else str(score)
        print(f"  {i:2}. {m['title']:<40} score={score_str:<6} {genres}")

    # Genre coverage — how many positive-seed genres appear in recs
    async with AsyncSessionLocal() as db:
        positive_ids = [int(k) for k, v in ratings.items() if v == "positive"]
        result = await db.execute(
            select(Movie).where(Movie.tmdb_id.in_(positive_ids))
        )
        seed_movies = result.scalars().all()
        seed_genres  = {g for m in seed_movies for g in (m.genres or [])}
        rec_genres   = set(all_genres)
        overlap      = seed_genres & rec_genres
        coverage = len(overlap) / len(seed_genres) if seed_genres else 0.0
        print(f"\nGenre coverage: {coverage:.0%} "
              f"({len(overlap)}/{len(seed_genres)} seed genres represented)")
        print(f"Seed genres : {', '.join(sorted(seed_genres))}")
        print(f"Covered     : {', '.join(sorted(overlap))}")


async def main():
    parser = argparse.ArgumentParser(description="Test guest feed recommendation quality")
    parser.add_argument(
        "--ratings", type=str,
        help='JSON string: {"tmdb_id": "positive|neutral|negative"}',
    )
    parser.add_argument("--user-id", type=int, help="Use ratings from DB user")
    parser.add_argument(
        "--preset", choices=list(PRESETS.keys()), help="Use preset profile",
    )
    args = parser.parse_args()

    if args.user_id:
        ratings = await get_ratings_from_user(args.user_id)
        print(f"Loaded {len(ratings)} ratings from user {args.user_id}")
    elif args.preset:
        ratings = PRESETS[args.preset]
        print(f"Using preset: {args.preset}")
    elif args.ratings:
        ratings = json.loads(args.ratings)
    else:
        parser.error("Provide --ratings, --user-id, or --preset")

    await evaluate_feed(ratings)


if __name__ == "__main__":
    asyncio.run(main())
