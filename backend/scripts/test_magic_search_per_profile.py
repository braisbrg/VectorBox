"""Test #1 of the F-23 synthetic-profile sweep — Magic Search per profile.

For each synthetic user, runs a small panel of representative queries
through the full Magic Search pipeline (LLM intent → embedding →
Qdrant → Sprint 1+2 filters → Sprint 3 blend + re-sort) and prints the
top results. Excludes the user's rated anchors so we see real
recommendations, not the seeds.

Usage:
    docker compose exec backend python scripts/test_magic_search_per_profile.py
"""
import asyncio
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from qdrant_client.models import SearchParams

from config import AsyncSessionLocal
from models.database import Movie, User, UserRating
from services.embedding_service import EmbeddingService
from services.magic_search_ranking import compute_blended_score, movie_passes_post_filter
from services.nlp_search import parse_user_intent
from services.qdrant_service import QdrantService


# Queries each profile should be able to "feel" — picks intentionally test
# multiple filter dimensions per profile.
QUERY_PANEL = {
    "gangster": [
        "oscar-winning crime drama",
        "italian-american mafia films",
        "highly rated thrillers from the 90s",
    ],
    "psychological_horror": [
        "BAFTA-winning psychological horror",
        "korean cinema horror",
        "slow-burn unsettling films",
    ],
    "plot_twist": [
        "Christopher Nolan style mind-bending thrillers",
        "movies with shocking endings",
        "neo-noir psychological mysteries",
    ],
    "family_animated": [
        "family-friendly studio Ghibli style",
        "Pixar animated adventures",
        "kids films rated G",
    ],
    "cine_quinqui": [
        "cine quinqui",
        "Spanish social drama",
        "películas en gallego",
    ],
    "french_arthouse": [
        "cine francés intimista",
        "French new wave classics",
        "European art-house cinema",
    ],
}


async def _watched_tmdb_ids(user_id: int) -> set[int]:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Movie.tmdb_id)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(UserRating.user_id == user_id)
        )).all()
    return {r[0] for r in rows if r[0] is not None}


async def _run_query(query: str, user_id: int) -> list[tuple[float, Movie]]:
    intent = await parse_user_intent(query)
    emb = EmbeddingService()
    qd = QdrantService()

    vec = emb.generate_embedding(
        {"overview": intent.semantic_query, "genres": intent.include_genres or [], "keywords": []},
        text_override=intent.semantic_query,
    )
    hits = await qd.client.query_points(
        collection_name="movies", query=vec.tolist(), limit=40,
        search_params=SearchParams(hnsw_ef=128),
    )

    watched = await _watched_tmdb_ids(user_id)
    tmdb_ids = [int(h.payload.get("tmdb_id") or h.id) for h in hits.points]
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Movie).where(Movie.tmdb_id.in_(tmdb_ids)))).scalars().all()
        by_tmdb = {m.tmdb_id: m for m in rows}

    scored: list[tuple[float, Movie]] = []
    for h in hits.points:
        tid = int(h.payload.get("tmdb_id") or h.id)
        if tid in watched:
            continue
        m = by_tmdb.get(tid)
        if m is None or not movie_passes_post_filter(m, intent):
            continue
        final, _, _ = compute_blended_score(
            raw_cosine=h.score, query=query, intent=intent,
            title=m.title or "", vbs=m.vectorbox_score,
        )
        scored.append((final, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


async def main():
    async with AsyncSessionLocal() as db:
        users = (await db.execute(
            select(User.id, User.username).where(User.username.like("synthetic_%"))
        )).all()
    users_by_key = {u.username.replace("synthetic_", ""): u.id for u in users}

    for profile_key, queries in QUERY_PANEL.items():
        uid = users_by_key.get(profile_key)
        if uid is None:
            print(f"\n[skip] {profile_key}: synthetic user not found (re-seed with scripts/synthetic_profiles.py).")
            continue
        print("\n" + "=" * 70)
        print(f"PROFILE: {profile_key}  (user_id={uid})")
        print("=" * 70)
        for q in queries:
            print(f"\n  Q: {q!r}")
            results = await _run_query(q, uid)
            for final, m in results[:5]:
                g = ", ".join((m.genres or [])[:3])
                print(f"    {final:6.1f}  {m.title[:36]:36s} ({m.year})  vbs={(m.vectorbox_score or 0):.0f}  [{g}]")
            if not results:
                print("    (no results)")


asyncio.run(main())
