"""Magic Search end-to-end validation harness.

Runs a handful of representative queries through the same pipeline the API
uses (parse_user_intent → embedding → Qdrant → Sprint 1+2 DB post-filter →
Sprint 3 blend + sort) and prints the top results with their scores so you
can eyeball ordering changes without a frontend.

Usage:
    docker compose exec backend python scripts/validate_magic_search.py
"""
import asyncio
import math
import os
import sys
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from qdrant_client.models import SearchParams

from config import AsyncSessionLocal
from models.database import Movie
from services.embedding_service import EmbeddingService
from services.nlp_search import parse_user_intent
from services.qdrant_service import QdrantService
from utils.scoring import normalize_similarity_score


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


def _descriptive_filters_set(intent) -> bool:
    return any((
        intent.year_min, intent.year_max, intent.include_genres,
        intent.min_runtime_minutes, intent.max_runtime_minutes,
        intent.min_rating, intent.original_language,
        intent.mpaa_ratings, intent.min_oscar_wins, intent.min_imdb_rating,
        intent.min_metacritic, intent.countries, intent.spoken_languages,
        intent.awards_contains,
    ))


def _apply_blend(query: str, intent, raw_score: float, metadata: dict, db_movie):
    """Replicates routers/search.py blending so the validation matches prod."""
    final = normalize_similarity_score(raw_score)

    title_boost_eligible = (
        not intent.reference_movie
        and not _descriptive_filters_set(intent)
        and len(query.strip()) <= 40
    )
    title_sim = None
    if title_boost_eligible:
        title_sim = SequenceMatcher(None, query.lower(), (metadata.get("title") or "").lower()).ratio()
        if title_sim >= 0.85:
            title_score = 90 + (title_sim * 9)
            final = final * 0.7 + title_score * 0.3

    # NULL VBS is treated as 0 (lowest known quality) so films without OMDb
    # data don't bypass the gate. Matches routers/search.py.
    vb = (db_movie.vectorbox_score if db_movie else None) or 0
    if intent.quality_gate_bypass:
        midpoint, steepness, floor = 25, 0.10, 0.10
    else:
        midpoint, steepness, floor = 55, 0.10, 0.20
    sigmoid = 1.0 / (1.0 + math.exp(-steepness * (vb - midpoint)))
    weight = floor + (1.0 - floor) * sigmoid
    final = final * weight

    return final, title_sim, weight


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

        complexity = sum(
            1 for v in (
                intent.include_genres, intent.year_min, intent.year_max,
                intent.min_runtime_minutes, intent.max_runtime_minutes,
                intent.min_rating, intent.original_language,
                intent.reference_movie, intent.mpaa_ratings,
                intent.min_oscar_wins, intent.min_imdb_rating,
                intent.min_metacritic, intent.countries,
                intent.spoken_languages, intent.awards_contains,
            ) if v
        ) + (1 if intent.popularity_vibe != "any" else 0)
        print(f"  intent_complexity = {complexity}  (auto-deep when ≥3)")

        vec = emb.generate_embedding(
            {"overview": intent.semantic_query, "genres": intent.include_genres or [], "keywords": []},
            text_override=intent.semantic_query,
        )

        hits = await qd.client.query_points(
            collection_name="movies", query=vec.tolist(), limit=20,
            search_params=SearchParams(hnsw_ef=128),
        )

        # Resolve DB rows for blending (vbs + safe_mode + post-filters)
        tmdb_ids = [h.payload.get("tmdb_id") for h in hits.points if h.payload.get("tmdb_id")]
        db_movies = {}
        if tmdb_ids:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(select(Movie).where(Movie.tmdb_id.in_(tmdb_ids)))).scalars().all()
                db_movies = {m.tmdb_id: m for m in rows}

        # Sprint 1+2 post-filter
        allowed_mpaa = set(intent.mpaa_ratings) if intent.mpaa_ratings else None
        wanted_countries = set(intent.countries) if intent.countries else None
        wanted_langs = set(intent.spoken_languages) if intent.spoken_languages else None
        awards_needles = [s.lower() for s in (intent.awards_contains or [])]

        results = []
        for h in hits.points:
            tid = h.payload.get("tmdb_id") or h.id
            m = db_movies.get(tid)
            if m is None:
                continue
            if intent.safe_mode and bool(m.is_adult):
                continue
            if allowed_mpaa is not None and (m.mpaa_rating or "") not in allowed_mpaa:
                continue
            if intent.min_oscar_wins and (m.oscar_wins or 0) < intent.min_oscar_wins:
                continue
            if wanted_countries is not None:
                if set(m.omdb_countries or []).isdisjoint(wanted_countries):
                    continue
            if wanted_langs is not None:
                if set(m.omdb_languages or []).isdisjoint(wanted_langs):
                    continue
            if awards_needles:
                a = (m.awards_text or "").lower()
                if not all(s in a for s in awards_needles):
                    continue

            final, title_sim, weight = _apply_blend(query, intent, h.score, h.payload, m)
            results.append((final, h.score, title_sim, weight, m))

        # Pre-sort view
        print(f"  top 5 by RAW Qdrant cosine (pre-Sprint-3):")
        for h in hits.points[:5]:
            p = h.payload
            print(f"    {h.score:.3f}  {p.get('title','?')[:50]:50s} vbs={p.get('vectorbox_score') or 0:.0f}")

        # Sorted by final_score
        results.sort(key=lambda x: x[0], reverse=True)
        print(f"  top 5 by BLENDED final_score (Sprint 3 active):")
        for final, raw, ts, w, m in results[:5]:
            ts_str = f" title_sim={ts:.2f}" if ts is not None else ""
            print(f"    {final:6.1f}  raw={raw:.3f} w={w:.2f}{ts_str}  {m.title[:45]:45s} vbs={(m.vectorbox_score or 0):.0f}")


asyncio.run(main())
