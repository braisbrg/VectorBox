"""
QA Seed Script — populates QA_VecBox with synthetic ratings
covering all test scenarios for QA E2E pass.
Safe to re-run: uses upsert logic throughout.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AsyncSessionLocal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models.database import User, Movie, UserRating
from services.movie_service import MovieService
from services.clustering_service import ClusteringService
from services.qdrant_service import QdrantService

QA_USERNAME = "qa_vecbox"

# Seed data — covers all QA test cases
RATINGS = [
    # 5-star seeds — Signal C crowd (needs rating == 5.0)
    {"tmdb_id": 238,    "rating": 5.0},   # The Godfather
    {"tmdb_id": 496243, "rating": 5.0},   # Parasite
    {"tmdb_id": 129,    "rating": 5.0},   # Spirited Away
    {"tmdb_id": 62,     "rating": 5.0},   # 2001: A Space Odyssey
    {"tmdb_id": 335984, "rating": 5.0},   # Blade Runner 2049

    # 4-4.5 star seeds — Signal A vibe + clustering
    {"tmdb_id": 27205,  "rating": 4.5},   # Inception
    {"tmdb_id": 157336, "rating": 4.5},   # Interstellar
    {"tmdb_id": 155,    "rating": 4.5},   # The Dark Knight
    {"tmdb_id": 1018,   "rating": 4.0},   # Mulholland Drive
    {"tmdb_id": 153,    "rating": 4.0},   # Lost in Translation
    {"tmdb_id": 38,     "rating": 4.5},   # Eternal Sunshine
    {"tmdb_id": 1422,   "rating": 4.5},   # There Will Be Blood
    {"tmdb_id": 6966,   "rating": 4.5},   # No Country for Old Men
    {"tmdb_id": 57012,  "rating": 4.0},   # Drive
    {"tmdb_id": 152601, "rating": 4.0},   # Her

    # liked but no rating — tests is_liked fix (F6)
    {"tmdb_id": 670,    "rating": None, "is_liked": True},   # Oldboy
    {"tmdb_id": 183011, "rating": None, "is_liked": True},   # The Act of Killing
    {"tmdb_id": 376867, "rating": None, "is_liked": True},   # Moonlight
    {"tmdb_id": 556574, "rating": None, "is_liked": True},   # Portrait of a Lady on Fire

    # 2-3 star — negative signal for wildcard exclusion
    {"tmdb_id": 1858,   "rating": 2.0},   # Transformers
    {"tmdb_id": 168259, "rating": 2.5},   # Fast & Furious 7

    # watchlist — tests available_now + random_watchlist
    {"tmdb_id": 771443,  "rating": None, "is_watchlist": True},  # Tár
    {"tmdb_id": 1008042, "rating": None, "is_watchlist": True},  # Aftersun
    {"tmdb_id": 674324,  "rating": None, "is_watchlist": True},  # The Banshees
    {"tmdb_id": 1060473, "rating": None, "is_watchlist": True},  # Past Lives
]

async def seed():
    stats = {
        "movies_processed": 0,
        "ratings_inserted": 0,
        "ratings_updated": 0,
        "errors": 0
    }

    async with AsyncSessionLocal() as db:
        # 1. Find or Create User
        result = await db.execute(
            select(User).where(User.username == QA_USERNAME)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            print(f"User '{QA_USERNAME}' not found. Creating synthetic QA user...")
            from passlib.context import CryptContext
            pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
            user = User(
                username=QA_USERNAME,
                email="qa@vecbox.local",
                pin_hash=pwd_context.hash("qa_password123")
            )
            db.add(user)
            await db.flush() # Get user.id
            print(f"Created user: {QA_USERNAME} (id={user.id})")
        else:
            print(f"Found existing user: {QA_USERNAME} (id={user.id})")

        user_id = user.id

        movie_service = MovieService(db)

        for entry in RATINGS:
            tmdb_id = entry["tmdb_id"]
            rating_val = entry.get("rating")
            is_liked = entry.get("is_liked", False)
            is_watchlist = entry.get("is_watchlist", False)
            is_watched = not is_watchlist

            try:
                # Ensure movie exists in DB + Qdrant
                movie = await movie_service.get_or_create_movie(tmdb_id)
                if not movie:
                    print(f"  SKIP  tmdb:{tmdb_id} — could not ingest")
                    stats["errors"] += 1
                    continue

                stats["movies_processed"] += 1

                # Upsert rating
                r_result = await db.execute(
                    select(UserRating).where(
                        UserRating.user_id == user_id,
                        UserRating.movie_id == movie.id
                    )
                )
                existing = r_result.scalar_one_or_none()

                if existing:
                    existing.rating = rating_val
                    existing.is_liked = is_liked
                    existing.is_watchlist = is_watchlist
                    existing.is_watched = is_watched
                    stats["ratings_updated"] += 1
                    label = "UPDATE"
                else:
                    new_rating = UserRating(
                        user_id=user_id,
                        movie_id=movie.id,
                        rating=rating_val,
                        is_liked=is_liked,
                        is_watchlist=is_watchlist,
                        is_watched=is_watched
                    )
                    db.add(new_rating)
                    stats["ratings_inserted"] += 1
                    label = "INSERT"

                await db.commit()

                score_str = f"{rating_val}★" if rating_val else (
                    "❤ liked" if is_liked else "📋 watchlist"
                )
                print(f"  {label}  {movie.title} ({movie.year}) — {score_str}")

            except Exception as e:
                print(f"  ERROR  tmdb:{tmdb_id} — {e}")
                stats["errors"] += 1
                await db.rollback()

        # 2. Run clustering
        print("\nRunning K-Means clustering...")
        try:
            qdrant = QdrantService()
            clustering = ClusteringService(qdrant=qdrant)
            clusters = await clustering.create_user_clusters(user_id, db)
            print(f"Clustering complete: {len(clusters)} cluster(s) created")
        except Exception as e:
            print(f"Clustering failed: {e}")
            clusters = []

    # Summary
    print("\n" + "="*50)
    print(f"Movies processed : {stats['movies_processed']}")
    print(f"Ratings inserted : {stats['ratings_inserted']}")
    print(f"Ratings updated  : {stats['ratings_updated']}")
    print(f"Errors           : {stats['errors']}")
    print(f"Clusters created : {len(clusters)}")
    print()

    ok = (
        stats["movies_processed"] >= 20
        and stats["errors"] == 0
        and len(clusters) >= 1
    )
    if ok:
        print("Ready for QA: ✅")
    else:
        print("Ready for QA: ❌  — check errors above")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(seed())
