"""Magic Search end-to-end validation harness.

Runs a handful of representative queries through the same pipeline the API
uses (parse_user_intent → embedding → Qdrant → Sprint 1+2 DB post-filter →
Sprint 3 blend + sort) and prints the top results with their scores so you
can eyeball ordering changes without a frontend.

Usage:
    docker compose exec backend python scripts/validate_magic_search.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from qdrant_client.models import SearchParams

from config import AsyncSessionLocal
from models.database import Movie
from services.embedding_service import EmbeddingService
from services.magic_search_ranking import (
    compute_blended_score,
    intent_complexity,
    movie_passes_post_filter,
)
from services.nlp_search import parse_user_intent
from services.qdrant_service import QdrantService


QUERIES = os.environ.get("MAGIC_QUERIES", "").split("|") if os.environ.get("MAGIC_QUERIES") else [
    "películas como Deprisa, deprisa",
    "Inception",                       # literal-title lookup — title-boost SHOULD apply
    "Godfather",                       # same — literal title
    "películas oscuras y psicológicas como Haneke",
    "cine francés intimista",
    "oscar-winning thrillers from the 90s",
    "family-friendly animated adventures",
    "highly rated korean cinema",
    "cine quinqui",
    "películas en gallego",
]


async def main():
    emb = EmbeddingService()
    qd = QdrantService()

    for query in QUERIES:
        print(f"\n========== {query!r} ==========")
        try:
            intent = await parse_user_intent(query)
        except Exception as e:
            print(f"  [intent failed: {e}]")
            continue

        active = []
        for fld in (
            "year_min", "year_max", "include_genres", "min_rating",
            "popularity_vibe", "original_language", "reference_movie",
            "mpaa_ratings", "min_oscar_wins", "min_imdb_rating",
            "min_metacritic", "countries", "spoken_languages", "awards_contains",
        ):
            v = getattr(intent, fld, None)
            if v not in (None, False, "any", []):
                active.append(f"{fld}={v!r}")
        print(f"  intent.semantic_query = {intent.semantic_query!r}")
        print(f"  intent.reference_movie = {intent.reference_movie!r}")
        print(f"  filters: {', '.join(active) if active else '(none)'}")
        print(f"  intent_complexity = {intent_complexity(intent)}  (auto-deep when ≥3)")

        vec = emb.generate_embedding(
            {"overview": intent.semantic_query, "genres": intent.include_genres or [], "keywords": []},
            text_override=intent.semantic_query,
        )

        hits = await qd.client.query_points(
            collection_name="movies", query=vec.tolist(), limit=20,
            search_params=SearchParams(hnsw_ef=128),
        )

        tmdb_ids = [h.payload.get("tmdb_id") for h in hits.points if h.payload.get("tmdb_id")]
        db_movies = {}
        if tmdb_ids:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(select(Movie).where(Movie.tmdb_id.in_(tmdb_ids)))).scalars().all()
                db_movies = {m.tmdb_id: m for m in rows}

        results = []
        for h in hits.points:
            tid = h.payload.get("tmdb_id") or h.id
            m = db_movies.get(tid)
            if m is None or not movie_passes_post_filter(m, intent):
                continue
            final, ts, weight = compute_blended_score(
                raw_cosine=h.score, query=query, intent=intent,
                title=m.title or "", vbs=m.vectorbox_score,
            )
            results.append((final, h.score, ts, weight, m))

        print(f"  top 5 by RAW Qdrant cosine (pre-Sprint-3):")
        for h in hits.points[:5]:
            p = h.payload
            print(f"    {h.score:.3f}  {p.get('title','?')[:50]:50s} vbs={p.get('vectorbox_score') or 0:.0f}")

        results.sort(key=lambda x: x[0], reverse=True)
        print(f"  top 5 by BLENDED final_score (Sprint 3 active):")
        for final, raw, ts, w, m in results[:5]:
            ts_str = f" title_sim={ts:.2f}" if ts is not None else ""
            print(f"    {final:6.1f}  raw={raw:.3f} w={w:.2f}{ts_str}  {m.title[:45]:45s} vbs={(m.vectorbox_score or 0):.0f}")


asyncio.run(main())
