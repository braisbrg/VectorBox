"""Hermetic tests for GET /api/recommendations/why/{tmdb_id} (ACID why-this).

Covers:
  - response shape + trident normalization (sums to ~1) on the happy path
  - neighbors sorted by ascending distance; anchors restricted to 3.5★+
  - graceful degrade when the film has no stored Qdrant vector
  - 404 when the film is not in the catalogue

All hermetic: no DB, no Qdrant, no Redis (mock stubs only).
"""
import os
import sys
from datetime import datetime

import pytest

sys.path.append(os.getcwd())

from unittest.mock import AsyncMock, Mock

from fastapi import HTTPException


def _movie(internal_id, tmdb_id, title, **extra):
    from models.database import Movie
    m = Movie(tmdb_id=tmdb_id, title=title, genres=extra.pop("genres", ["Drama"]), **extra)
    m.id = internal_id
    return m


def _rating(rating, watch_count=1, watched_date=None, created_at=None):
    r = Mock()
    r.rating = rating
    r.watch_count = watch_count
    r.watched_date = watched_date or datetime(2026, 3, 1)
    r.created_at = created_at or datetime(2026, 3, 1)
    return r


def _db_with_results(results):
    """AsyncSession stub: each execute() pops the next prepared result."""
    db = Mock()
    db.execute = AsyncMock(side_effect=results)
    return db


def _scalar_result(value):
    res = Mock()
    res.scalar_one_or_none.return_value = value
    return res


def _rows_result(rows):
    res = Mock()
    res.all.return_value = rows
    return res


def _scalars_all_result(values):
    res = Mock()
    res.scalars.return_value.all.return_value = values
    return res


def _handler():
    from routers.recommendations import why_this_film
    return why_this_film.__wrapped__ if hasattr(why_this_film, "__wrapped__") else why_this_film


def _token(user_id=1):
    from models.schemas import TokenResponse
    return TokenResponse(token="t", user_id=user_id, username="hermetic")


@pytest.mark.asyncio
async def test_why_happy_path_shape_and_ordering():
    target = _movie(1, 100, "Target Film", vectorbox_score=88.0, directors=["Jane Doe"], imdb_vote_count=3000)

    near = _movie(2, 201, "Nearest", directors=["Jane Doe"])
    mid = _movie(3, 202, "Mid")
    low_rated = _movie(4, 203, "Low Rated")  # 2★ → excluded from anchors, still a neighbor

    rated_rows = [
        (_rating(4.5), near),
        (_rating(4.0), mid),
        (_rating(2.0), low_rated),
    ]

    db = _db_with_results([
        _scalar_result(target),          # target movie lookup
        _rows_result(rated_rows),        # rated library
        _scalars_all_result([]),         # user clusters (none)
    ])

    qdrant = Mock()
    qdrant.get_vector = AsyncMock(return_value=[1.0, 0.0, 0.0])
    qdrant.get_vectors_batch = AsyncMock(return_value={
        201: [0.9, 0.1, 0.0],   # most similar
        202: [0.5, 0.5, 0.0],
        203: [0.0, 1.0, 0.0],   # orthogonal
    })

    out = await _handler()(request=None, tmdb_id=100, current_user=_token(), db=db, qdrant=qdrant)

    # trident normalizes to ~1
    t = out["trident"]
    assert abs(t["vibe"] + t["auteur"] + t["gems"] - 1.0) < 0.03
    # auteur signal fired: "Jane Doe" also directed a 4★+ film in the library
    assert out["auteur"] is not None and out["auteur"]["name"] == "Jane Doe"
    # neighbors sorted ascending by distance, nearest first
    dists = [n["dist"] for n in out["neighbors"]]
    assert dists == sorted(dists)
    assert out["neighbors"][0]["tmdb_id"] == 201
    # anchors exclude the 2★ film and weights sum to ~1
    anchor_ids = {a["tmdb_id"] for a in out["anchors"]}
    assert 203 not in anchor_ids
    assert abs(sum(a["weight"] for a in out["anchors"]) - 1.0) < 0.05
    # rank not yet computable → explicit nulls (UI hides)
    assert out["rank"] is None and out["rank_pool"] is None


@pytest.mark.asyncio
async def test_why_degrades_without_stored_vector():
    target = _movie(1, 100, "No Vector Film", vectorbox_score=90.0, imdb_vote_count=1000)
    db = _db_with_results([
        _scalar_result(target),
        _rows_result([(_rating(5.0), _movie(2, 201, "Rated"))]),
        _scalars_all_result([]),
    ])
    qdrant = Mock()
    qdrant.get_vector = AsyncMock(return_value=None)
    qdrant.get_vectors_batch = AsyncMock(return_value={})

    out = await _handler()(request=None, tmdb_id=100, current_user=_token(), db=db, qdrant=qdrant)

    assert out["anchors"] == [] and out["neighbors"] == []
    assert out["cluster"] is None
    t = out["trident"]
    assert abs(t["vibe"] + t["auteur"] + t["gems"] - 1.0) < 0.03


@pytest.mark.asyncio
async def test_why_404_when_movie_missing():
    db = _db_with_results([_scalar_result(None)])
    qdrant = Mock()

    with pytest.raises(HTTPException) as exc:
        await _handler()(request=None, tmdb_id=999999, current_user=_token(), db=db, qdrant=qdrant)
    assert exc.value.status_code == 404
