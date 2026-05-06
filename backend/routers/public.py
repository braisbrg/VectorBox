"""
Public Router — Endpoints exposed without authentication for guest users.

Endpoints:
    POST /public/guest-feed — Personalized recommendations from localStorage ratings
"""
import logging
from typing import Dict, List

import numpy as np
from fastapi import APIRouter, Depends, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_db
from dependencies import get_qdrant_service
from limiter import limiter
from models.database import Movie
from services.qdrant_service import QdrantService

logger = logging.getLogger(__name__)
router = APIRouter()


def _serialize(m: Movie) -> dict:
    return {
        "tmdb_id": m.tmdb_id,
        "title": m.title,
        "year": m.year,
        "poster_path": m.poster_path,
        "overview": m.overview,
        "genres": m.genres,
        "vectorbox_score": m.vectorbox_score,
        "vote_average": m.vote_average,
        "runtime": m.runtime,
        "directors": m.directors,
    }


async def _get_popular_fallback(db: AsyncSession) -> List[dict]:
    """Cold-start: highly-rated, very-popular films when the guest has no usable signal."""
    result = await db.execute(
        select(Movie)
        .where(Movie.vectorbox_score >= 65)
        .where(Movie.vote_count >= 5000)
        .where(Movie.poster_path.isnot(None))
        .order_by(desc(Movie.popularity))
        .limit(20)
    )
    return [_serialize(m) for m in result.scalars().all()]


async def _compute_guest_feed(
    ratings: Dict[int, str],
    db: AsyncSession,
    qdrant: QdrantService,
) -> List[dict]:
    """
    Core guest feed computation. Callable from both the HTTP endpoint and
    test scripts.

    Returns a list of serialized movie dicts ordered by similarity to the
    centroid of positive-signal embeddings. Falls back to popular films when
    fewer than 3 positive ratings exist or no Qdrant vectors are found.
    """
    positive_ids = [tid for tid, sig in ratings.items() if sig == "positive"]
    if len(positive_ids) < 3:
        return await _get_popular_fallback(db)

    vectors_map = await qdrant.get_vectors_batch(positive_ids)
    vectors = [np.array(v) for v in vectors_map.values() if v]
    if not vectors:
        return await _get_popular_fallback(db)

    centroid = np.mean(vectors, axis=0).tolist()
    results = await qdrant.search_similar(
        query_vector=centroid,
        limit=50,
        score_threshold=0.3,
    )

    rated_ids = {int(k) for k in ratings.keys()}
    candidate_ids: List[int] = []
    for r in results:
        meta = r.get("metadata", {}) or {}
        rid = int(meta.get("tmdb_id") or r["movie_id"])
        if rid not in rated_ids and rid not in candidate_ids:
            candidate_ids.append(rid)
        if len(candidate_ids) >= 30:
            break

    if not candidate_ids:
        return await _get_popular_fallback(db)

    movies_q = await db.execute(
        select(Movie)
        .where(Movie.tmdb_id.in_(candidate_ids))
        .where(Movie.vectorbox_score >= 55)
        .where(Movie.vote_count >= 100)
        .where(Movie.poster_path.isnot(None))
    )
    by_id = {m.tmdb_id: m for m in movies_q.scalars().all()}

    output: List[dict] = []
    for rid in candidate_ids:
        m = by_id.get(rid)
        if m:
            output.append(_serialize(m))
            if len(output) >= 20:
                break

    return output if output else await _get_popular_fallback(db)


@router.post("/public/guest-feed")
@limiter.limit("10/minute")
async def get_guest_feed(
    request: Request,
    ratings: Dict[int, str],
    db: AsyncSession = Depends(get_db),
    qdrant: QdrantService = Depends(get_qdrant_service),
):
    """
    Generate a personalized feed for guest users from localStorage ratings.
    Body: {tmdb_id: "positive" | "neutral" | "negative"}
    Falls back to popular films when fewer than 3 positive ratings or no
    usable vectors are found.
    """
    return await _compute_guest_feed(ratings, db, qdrant)
