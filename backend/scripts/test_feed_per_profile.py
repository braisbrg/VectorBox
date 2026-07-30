"""Test #2 of the F-23 sweep — full main feed per synthetic profile.

Calls FeedService.get_main_feed for each synthetic user and prints the
top 4 picks per section. Catches regressions in any of the engine signals
(Signal A vibe, Auteur, Hidden Gems, Niche Picks, Available Now, etc.)
that a single Magic Search query wouldn't surface.

Usage:
    docker compose exec backend python scripts/test_feed_per_profile.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import User
from services.feed_service import FeedService
from services.qdrant_service import QdrantService
from services.tmdb_client import TMDBClient


async def main():
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(User.id, User.username).where(User.username.like("synthetic_%"))
        )).all()
    users = [(u.id, u.username.replace("synthetic_", "")) for u in rows]

    if not users:
        print("No synthetic users — run scripts/synthetic_profiles.py first.")
        return

    from services.embedding_service import EmbeddingService
    qdrant = QdrantService()
    tmdb = TMDBClient()
    embedding = EmbeddingService()
    # FeedService constructs RecommendationEngine with the qdrant/embedding
    # we pass — engine methods rely on `self.qdrant` / `self.embedding_service`
    # being non-None. Mirrors how FastAPI Depends() wires them in production.
    feed_service = FeedService(qdrant=qdrant, embedding_service=embedding)

    try:
        for uid, key in users:
            print("\n" + "=" * 70)
            print(f"PROFILE: {key}  (user_id={uid})")
            print("=" * 70)
            try:
                feed = await feed_service.get_main_feed(
                    user_id=uid,
                    country_code="ES",
                    streaming_providers=[],
                    tmdb=tmdb,
                    qdrant=qdrant,
                )
            except Exception as e:
                print(f"  [error] get_main_feed failed: {e}")
                continue

            for section in feed.feed:
                items = section.items[:4]
                if not items:
                    print(f"  {section.title!r}: (empty)")
                    continue
                print(f"\n  {section.title!r}")
                for it in items:
                    score = it.match_score or 0
                    vbs = it.vectorbox_score or 0
                    title = (it.title or "?")[:38]
                    print(f"    score={score:>3.0f} vbs={vbs:>3.0f}  {title:38s} ({it.year})")
    finally:
        await tmdb.aclose()


asyncio.run(main())
