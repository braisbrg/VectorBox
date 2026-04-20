"""
QA Seed Script — populates QA_VecBox with synthetic ratings
covering all test scenarios for QA E2E pass.
Safe to re-run: uses upsert logic throughout.

Movie pool is intentionally large (~80 films) so that after
cross-section deduplication in get_main_feed(), at least 3
feed sections still have unique items. With <30 movies the
pool is exhausted by section 2.
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

# ── RATINGS ────────────────────────────────────────────────────────────────
# Pool is large so cross-section dedup in the feed doesn't exhaust it.
# Variety of genres/directors/eras ensures signals diverge.
RATINGS = [
    # ── 5★ seeds — Signal C + clustering centroid ──────────────────────────
    {"tmdb_id": 238,    "rating": 5.0},   # The Godfather
    {"tmdb_id": 496243, "rating": 5.0},   # Parasite
    {"tmdb_id": 129,    "rating": 5.0},   # Spirited Away
    {"tmdb_id": 335984, "rating": 5.0},   # Blade Runner 2049
    {"tmdb_id": 278,    "rating": 5.0},   # The Shawshank Redemption
    {"tmdb_id": 240,    "rating": 5.0},   # The Godfather Part II
    {"tmdb_id": 424,    "rating": 5.0},   # Schindler's List
    {"tmdb_id": 680,    "rating": 5.0},   # Pulp Fiction
    {"tmdb_id": 372058, "rating": 5.0},   # Your Name (anime variety)
    {"tmdb_id": 637,    "rating": 5.0},   # Life is Beautiful

    # ── 4–4.5★ seeds — Signal A (item-item CF) + auteur ───────────────────
    {"tmdb_id": 27205,  "rating": 4.5},   # Inception          (Nolan)
    {"tmdb_id": 157336, "rating": 4.5},   # Interstellar       (Nolan)
    {"tmdb_id": 155,    "rating": 4.5},   # The Dark Knight    (Nolan)
    {"tmdb_id": 49026,  "rating": 4.0},   # The Dark Knight Rises (Nolan)
    {"tmdb_id": 77,     "rating": 4.5},   # Memento            (Nolan)
    {"tmdb_id": 1018,   "rating": 4.0},   # Mulholland Drive   (Lynch)
    {"tmdb_id": 153,    "rating": 4.0},   # Lost in Translation (Coppola)
    {"tmdb_id": 38,     "rating": 4.5},   # Eternal Sunshine   (Gondry)
    {"tmdb_id": 550,    "rating": 4.5},   # Fight Club         (Fincher)
    {"tmdb_id": 807,    "rating": 4.5},   # Se7en              (Fincher)
    {"tmdb_id": 146233, "rating": 4.5},   # Prisoners          (Villeneuve)
    {"tmdb_id": 264660, "rating": 4.5},   # Ex Machina         (Garland)
    {"tmdb_id": 244786, "rating": 4.5},   # Whiplash           (Chazelle)
    {"tmdb_id": 769,    "rating": 4.5},   # GoodFellas         (Scorsese)
    {"tmdb_id": 745,    "rating": 4.0},   # Silence of the Lambs
    {"tmdb_id": 598,    "rating": 4.5},   # City of God
    {"tmdb_id": 76341,  "rating": 4.5},   # Mad Max: Fury Road
    {"tmdb_id": 324857, "rating": 4.5},   # Spider-Man: Spider-Verse
    {"tmdb_id": 475557, "rating": 4.0},   # Joker
    {"tmdb_id": 399055, "rating": 4.5},   # The Shape of Water (del Toro)
    {"tmdb_id": 381288, "rating": 4.5},   # Hell or High Water
    {"tmdb_id": 359940, "rating": 4.5},   # Three Billboards
    {"tmdb_id": 510,    "rating": 4.0},   # One Flew Over the Cuckoo's Nest
    {"tmdb_id": 603,    "rating": 4.0},   # The Matrix
    {"tmdb_id": 816,    "rating": 4.0},   # Requiem for a Dream
    {"tmdb_id": 205596, "rating": 4.0},   # The Imitation Game
    {"tmdb_id": 334533, "rating": 4.0},   # Captain Fantastic
    {"tmdb_id": 4935,   "rating": 4.5},   # Howl's Moving Castle (anime)
    # ── IDs verificados contra TMDB (los anteriores devolvían películas incorrectas) ──
    {"tmdb_id": 6977,   "rating": 4.5},   # No Country for Old Men (era 6966 → The White Dawn)
    {"tmdb_id": 64690,  "rating": 4.5},   # Drive 2011 (era 57012 → Living with the Dead)
    {"tmdb_id": 123678, "rating": 4.5},   # The Act of Killing (era 183011 → Justice League: Flashpoint)
    {"tmdb_id": 531428, "rating": 4.5},   # Portrait of a Lady on Fire (era 556574 → Hamilton 2025)
    {"tmdb_id": 817758, "rating": 4.5},   # Tár (era 771443 → Penislong)
    {"tmdb_id": 666277, "rating": 4.5},   # Past Lives (era 1060473 → Silence Of The 177)
    {"tmdb_id": 120,    "rating": 4.5},   # LOTR: Fellowship
    {"tmdb_id": 122,    "rating": 4.5},   # LOTR: Return of the King
    {"tmdb_id": 13,     "rating": 4.0},   # Forrest Gump
    {"tmdb_id": 11,     "rating": 4.0},   # Star Wars: A New Hope
    {"tmdb_id": 389,    "rating": 4.5},   # 12 Angry Men
    {"tmdb_id": 497,    "rating": 4.0},   # The Green Mile

    # ── Liked, no rating — tests is_liked fix (F6) ─────────────────────────
    {"tmdb_id": 670,    "rating": None, "is_liked": True},   # Oldboy
    {"tmdb_id": 376867, "rating": None, "is_liked": True},   # Moonlight
    {"tmdb_id": 346364, "rating": None, "is_liked": True},   # It (2017) — horror variety
    {"tmdb_id": 568,    "rating": None, "is_liked": True},   # Apollo 13

    # ── 2–3★ — negative signal for wildcard exclusion ─────────────────────
    {"tmdb_id": 1858,   "rating": 2.0},   # Transformers
    {"tmdb_id": 168259, "rating": 2.5},   # Furious 7
    {"tmdb_id": 9552,   "rating": 2.0},   # Saw
    {"tmdb_id": 8967,   "rating": 2.5},   # White Chicks

    # ── Watchlist — tests available_now + random_watchlist ─────────────────
    {"tmdb_id": 792307, "rating": None, "is_watchlist": True},  # Poor Things
    {"tmdb_id": 961268, "rating": None, "is_watchlist": True},  # Saltburn
    {"tmdb_id": 646389, "rating": None, "is_watchlist": True},  # Aftersun
    {"tmdb_id": 614934, "rating": None, "is_watchlist": True},  # Elvis
    {"tmdb_id": 545611, "rating": None, "is_watchlist": True},  # Everything Everywhere
    {"tmdb_id": 438631, "rating": None, "is_watchlist": True},  # Dune Part One
    {"tmdb_id": 693134, "rating": None, "is_watchlist": True},  # Dune Part Two
    {"tmdb_id": 940721, "rating": None, "is_watchlist": True},  # Godzilla Minus One
    {"tmdb_id": 872585, "rating": None, "is_watchlist": True},  # Oppenheimer
    {"tmdb_id": 346698, "rating": None, "is_watchlist": True},  # Barbie
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
            user = User(
                username=QA_USERNAME,
                email="qa@vecbox.local",
            )
            db.add(user)
            await db.flush()
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
                movie = await movie_service.get_or_create_movie(tmdb_id)
                if not movie:
                    print(f"  SKIP  tmdb:{tmdb_id} — could not ingest")
                    stats["errors"] += 1
                    continue

                stats["movies_processed"] += 1

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
    total = len(RATINGS)
    print("\n" + "=" * 50)
    print(f"Movies in seed    : {total}")
    print(f"Movies processed  : {stats['movies_processed']}")
    print(f"Ratings inserted  : {stats['ratings_inserted']}")
    print(f"Ratings updated   : {stats['ratings_updated']}")
    print(f"Errors            : {stats['errors']}")
    print(f"Clusters created  : {len(clusters)}")
    print()

    # Success threshold: >=80% processed, <=5 errors, >=2 clusters
    ok = (
        stats["movies_processed"] >= int(total * 0.8)
        and stats["errors"] <= 5
        and len(clusters) >= 2
    )
    if ok:
        print("Ready for QA: ✅")
    else:
        print("Ready for QA: ❌  — check errors above")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(seed())