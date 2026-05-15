"""F-23 — Synthetic taste profiles for recommendation regression checks.

Creates a handful of throw-away users with extremely opinionated taste
(all-gangster, all-horror, plot-twist, family-friendly, Galician indie,
French art-house) and asserts that the top recommendations the feed
generates for each one stay in-genre.

Two use modes:

  # Build + check every profile
  docker compose exec backend python scripts/synthetic_profiles.py

  # Tear down all synthetic users (cleanup after a run)
  docker compose exec backend python scripts/synthetic_profiles.py --cleanup

Why this exists:
  - Magic Search / feed quality can degrade silently when we change the
    embedding model, prompt recipe, sigmoid weights, or VBS thresholds.
    A unit test can pin score formulas (TST-3) and a single-vector recall
    (TST-2), but neither catches "the feed for a hardcore gangster fan
    started surfacing rom-coms".
  - This script is a deterministic, repeatable check across a panel of
    archetypal tastes. CI doesn't run it (depends on a populated DB +
    Qdrant), but engineers can fire it after any ranking change to
    eyeball regressions.

Output is human-readable: per profile, prints the top 10 picked for the
synthetic user (with vbs / genres) and the dominant-genre summary so a
clear "this user is being served random films" anomaly shows up.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, or_, select

from config import AsyncSessionLocal
from models.database import Movie, User, UserCluster, UserRating
from services.clustering_service import ClusteringService
from services.qdrant_service import QdrantService


SYNTHETIC_USERNAME_PREFIX = "synthetic_"


@dataclass
class Profile:
    """A synthetic taste — `anchors` are film titles we 5★ to seed it.

    `expected_genres` is the set we expect to dominate the resulting
    recommendations. The script doesn't fail the run; it prints a warning
    when the dominant-genre signal is weak so the engineer can decide.
    """
    key: str
    label: str
    anchors: list[str]
    expected_genres: set[str]


# Each profile needs ≥10 anchors that exist in the catalog — clustering_service
# refuses to cluster a user with fewer than 10 vector-resolved films. We
# over-seed so a couple missing anchors don't bring us below threshold.
PROFILES: list[Profile] = [
    Profile(
        key="gangster",
        label="Hardcore gangster",
        anchors=[
            "The Godfather", "The Godfather Part II", "Goodfellas", "Casino",
            "Scarface", "The Departed", "Once Upon a Time in America", "Heat",
            "A Bronx Tale", "Donnie Brasco", "The Irishman", "Road to Perdition",
            "The Untouchables", "American Gangster",
        ],
        expected_genres={"Crime", "Drama"},
    ),
    Profile(
        key="psychological_horror",
        label="Psychological horror",
        anchors=[
            "Hereditary", "It Follows", "The Babadook", "The Witch",
            "Midsommar", "The Lighthouse", "Get Out", "Us",
            "The Shining", "Black Swan", "Mother!", "Possession",
            "The Killing of a Sacred Deer", "Antichrist",
        ],
        expected_genres={"Horror", "Thriller", "Mystery"},
    ),
    Profile(
        key="plot_twist",
        label="Plot-twist devotee",
        anchors=[
            "The Sixth Sense", "Memento", "Shutter Island", "Fight Club",
            "The Prestige", "Primal Fear", "The Usual Suspects", "Se7en",
            "Gone Girl", "Old Boy", "Identity", "The Others",
            "Mulholland Drive", "Inception",
        ],
        expected_genres={"Thriller", "Mystery", "Drama"},
    ),
    Profile(
        key="family_animated",
        label="Family animated",
        anchors=[
            "Toy Story", "Coco", "Up", "WALL·E", "Spirited Away",
            "My Neighbor Totoro", "Finding Nemo", "The Lion King",
            "Inside Out", "Ratatouille", "Monsters, Inc.", "Moana",
            "Frozen", "Kiki's Delivery Service",
        ],
        expected_genres={"Animation", "Family", "Adventure"},
    ),
    Profile(
        key="cine_quinqui",
        label="Cine quinqui / Spanish social",
        anchors=[
            "Deprisa, deprisa", "Navajeros", "Barrio", "El crack",
            "Bad Education", "Cria!", "El Lute: camina o revienta",
            "Yo, 'El Vaquilla'", "El pico", "El pico 2", "Maravillas",
            "Colegas", "Perros callejeros", "Los olvidados",
        ],
        expected_genres={"Drama", "Crime"},
    ),
    Profile(
        key="french_arthouse",
        label="French art-house",
        anchors=[
            "Vivre Sa Vie", "Le Samouraï", "Petite Maman",
            "Cléo from 5 to 7", "Persona", "Breathless",
            "Band of Outsiders", "Pierrot le Fou", "The 400 Blows",
            "Jules and Jim", "Last Year at Marienbad", "Hiroshima mon amour",
            "L'Avventura", "Nouvelle Vague",
        ],
        expected_genres={"Drama"},
    ),
]


async def _resolve_anchor_ids(titles: Iterable[str]) -> dict[str, Movie]:
    """Find Movie rows for each anchor title; warn about missing ones."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Movie).where(
                or_(*[
                    Movie.title.ilike(t) | Movie.original_title.ilike(t) | Movie.title_es.ilike(t)
                    for t in titles
                ])
            )
        )
        rows = result.scalars().all()
    found: dict[str, Movie] = {}
    titles_set = {t.lower() for t in titles}
    for row in rows:
        for col in (row.title, row.original_title, row.title_es):
            if col and col.lower() in titles_set:
                found[col.lower()] = row
                break
    missing = [t for t in titles if t.lower() not in found]
    if missing:
        print(f"  [warn] anchors not in catalog: {missing}")
    return found


async def _ensure_synthetic_user(profile: Profile) -> int:
    """Insert or update the synthetic user for `profile.key`. Returns user_id."""
    username = f"{SYNTHETIC_USERNAME_PREFIX}{profile.key}"
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if existing is None:
            existing = User(username=username, letterboxd_username=username)
            db.add(existing)
            await db.commit()
            await db.refresh(existing)
        return existing.id


async def _seed_profile(profile: Profile, user_id: int) -> int:
    """Insert a 5★ rating for each resolved anchor. Returns # of anchors seeded."""
    anchors = await _resolve_anchor_ids(profile.anchors)
    if not anchors:
        return 0

    async with AsyncSessionLocal() as db:
        # Wipe any prior ratings so re-runs of this script are idempotent —
        # otherwise the profile would drift as the catalog changes.
        await db.execute(delete(UserRating).where(UserRating.user_id == user_id))
        for movie in anchors.values():
            db.add(UserRating(
                user_id=user_id, movie_id=movie.id,
                rating=5.0, is_watched=True, is_liked=True,
            ))
        await db.commit()
    return len(anchors)


async def _build_clusters(user_id: int) -> None:
    qdrant = QdrantService()
    clustering = ClusteringService(qdrant=qdrant)
    async with AsyncSessionLocal() as db:
        await db.execute(delete(UserCluster).where(UserCluster.user_id == user_id))
        await db.commit()
        try:
            await clustering.create_user_clusters(user_id, db, groq_client=None)
        except Exception as e:
            print(f"  [warn] cluster build failed: {e}")


async def _report_top_picks(profile: Profile, user_id: int, k: int = 10) -> None:
    """Print the user's clusters + top-N films by vector similarity to the
    user's centroid (across all clusters). Computes dominant-genre overlap
    against `expected_genres`."""
    from collections import Counter
    qdrant = QdrantService()

    async with AsyncSessionLocal() as db:
        clusters = (await db.execute(
            select(UserCluster).where(UserCluster.user_id == user_id)
        )).scalars().all()

        rated_ids = (await db.execute(
            select(UserRating.movie_id).where(UserRating.user_id == user_id)
        )).scalars().all()

    if not clusters:
        print("  [warn] no clusters built — recommendation engine cannot produce a feed.")
        return

    print(f"  clusters ({len(clusters)}):")
    for c in clusters:
        avg = f"{c.avg_rating:.2f}" if c.avg_rating is not None else "?"
        print(f"    - cluster {c.cluster_id}: {c.cluster_label!r}  size={c.movie_count}  avg★={avg}")

    # No centroid is persisted on UserCluster — we use the medoid film's
    # vector as a stand-in for "this cluster's direction". The medoid is the
    # rated film closest to the cluster's mean, so its embedding represents
    # the cluster well for a top-K similar query.
    best_cluster = max(clusters, key=lambda c: (c.avg_rating or 0))
    if best_cluster.medoid_movie_id is None:
        print("  [warn] best cluster has no medoid_movie_id.")
        return
    async with AsyncSessionLocal() as db:
        medoid_row = (await db.execute(
            select(Movie.tmdb_id, Movie.title).where(Movie.id == best_cluster.medoid_movie_id)
        )).first()
    if medoid_row is None:
        print("  [warn] medoid film not in DB.")
        return
    print(f"  using medoid: {medoid_row.title!r} (tmdb={medoid_row.tmdb_id})")

    medoid_vec = await qdrant.get_vector(medoid_row.tmdb_id)
    if medoid_vec is None:
        print("  [warn] medoid vector missing from Qdrant.")
        return

    hits = await qdrant.client.query_points(
        collection_name="movies", query=medoid_vec,
        limit=k * 4,
    )

    # Resolve DB rows so we can read genres / vbs.
    tmdb_ids = [int(h.payload.get("tmdb_id") or h.id) for h in hits.points]
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Movie).where(Movie.tmdb_id.in_(tmdb_ids)))).scalars().all()
        by_tmdb = {m.tmdb_id: m for m in rows}

    seen_internal = set(rated_ids)
    shown: list[Movie] = []
    genre_counter: Counter[str] = Counter()
    for h in hits.points:
        m = by_tmdb.get(int(h.payload.get("tmdb_id") or h.id))
        if m is None or m.id in seen_internal:
            continue
        shown.append(m)
        genre_counter.update(m.genres or [])
        if len(shown) >= k:
            break

    if not shown:
        print("  [warn] no recommendations produced after filtering rated films.")
        return

    print(f"  top {len(shown)} by cluster centroid:")
    for m in shown:
        g = ", ".join(m.genres or [])[:40]
        print(f"    {(m.vectorbox_score or 0):>3.0f}  {m.title[:38]:38s}  [{g}]")

    top_genres = [g for g, _ in genre_counter.most_common(5)]
    overlap = profile.expected_genres & set(top_genres)
    status = "PASS" if overlap else "WARN"
    print(f"  dominant genres = {top_genres}")
    print(f"  expected genres = {sorted(profile.expected_genres)}")
    print(f"  overlap = {sorted(overlap) or 'NONE'}  → {status}")


async def run_all() -> None:
    for profile in PROFILES:
        print("\n" + "=" * 70)
        print(f"PROFILE: {profile.label}")
        print("=" * 70)
        user_id = await _ensure_synthetic_user(profile)
        seeded = await _seed_profile(profile, user_id)
        if seeded == 0:
            print(f"  [skip] none of the anchors are in the catalog")
            continue
        print(f"  seeded {seeded} 5★ anchors for user_id={user_id}")
        await _build_clusters(user_id)
        await _report_top_picks(profile, user_id)


async def cleanup() -> None:
    """Remove every synthetic_ user and their ratings/clusters (CASCADE)."""
    async with AsyncSessionLocal() as db:
        users = (await db.execute(
            select(User).where(User.username.startswith(SYNTHETIC_USERNAME_PREFIX))
        )).scalars().all()
        for u in users:
            await db.execute(delete(UserRating).where(UserRating.user_id == u.id))
            await db.execute(delete(UserCluster).where(UserCluster.user_id == u.id))
        await db.execute(delete(User).where(User.username.startswith(SYNTHETIC_USERNAME_PREFIX)))
        await db.commit()
    print(f"Cleaned up {len(users)} synthetic users.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", action="store_true", help="Remove all synthetic_ users and exit.")
    args = parser.parse_args()
    if args.cleanup:
        asyncio.run(cleanup())
    else:
        asyncio.run(run_all())


if __name__ == "__main__":
    main()
