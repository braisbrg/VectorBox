"""Regression tests for the 2026-06-11 full-project review fixes:

  - REV-1  (tests removed in F5 2026-07-10 — _enrich_recommendations deleted
           with its orphan-endpoint consumers)
  - REV-2  Re-encoding a movie vector (ensure_vector_exists / enrich_movie)
           must pass text_override=cinematic_description when available, so a
           metadata refresh can't silently replace a Groq-enriched vector with
           the overview+genres+keywords fallback recipe.
  - REV-3  main.py shutdown closes Redis via the 4.x-compatible path
           (aclose() only exists from redis-py 5.0.1).
  - REV-6  mark_watched / reject_movie use atomic INSERT … ON CONFLICT
           (CONC-1 parity with /onboarding/rate).
  - REV-7  RecommendationService reuses the TraktClient module singleton.
  - REV-8  Background ingest helpers close their MovieService (releasing the
           lazily-created OMDb/Qdrant clients).

All hermetic: no DB, no network, no Redis server (mock stubs only).
"""
import os
import sys

import numpy as np
import pytest

sys.path.append(os.getcwd())

from unittest.mock import AsyncMock, MagicMock, Mock, patch


def _make_movie(internal_id: int, tmdb_id: int, title: str, **extra):
    from models.database import Movie
    movie = Movie(
        tmdb_id=tmdb_id,
        title=title,
        genres=extra.pop("genres", ["Drama"]),
        **extra,
    )
    movie.id = internal_id
    return movie


# ---------------------------------------------------------------------------
# REV-1 tests removed in F5 cleanup (2026-07-10): `_enrich_recommendations` was
# deleted along with its only consumers (the orphan POST /general, /by-mood,
# /random, /group endpoints — never called by the frontend).
# ---------------------------------------------------------------------------
# REV-2 — re-encode prefers cinematic_description (text_override)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_vector_exists_uses_cinematic_description_override():
    from services.movie_service import MovieService

    movie = _make_movie(
        1, 101, "Howl's Moving Castle",
        overview="A young woman is cursed...",
        cinematic_description="A tender, painterly fantasy about transformation.",
    )

    ms = MovieService(db=Mock(), tmdb=Mock())
    ms.tmdb.get_movie_keywords = AsyncMock(return_value=["magic"])
    ms._qdrant = Mock()
    ms._qdrant.get_vector = AsyncMock(return_value=None)  # vector missing → re-encode
    ms._qdrant.upsert_movie_vector = AsyncMock()
    ms._embedding = Mock()
    ms._embedding.generate_embedding = Mock(return_value=np.zeros(768))

    assert await ms.ensure_vector_exists(movie) is True

    _, kwargs = ms._embedding.generate_embedding.call_args
    assert kwargs.get("text_override") == movie.cinematic_description


def test_enrich_movie_reencode_passes_text_override():
    """Source guard: the enrich_movie re-encode block must thread
    text_override through to generate_embedding (behavioural test of the
    full enrich pipeline needs DB+OMDb; the recipe choice is what matters)."""
    with open(os.path.join("services", "movie_service.py"), encoding="utf-8") as f:
        src = f.read()
    # Both re-encode sites pass the override.
    assert src.count("text_override=text_override") >= 2
    assert src.count("movie.cinematic_description or None") >= 2


# ---------------------------------------------------------------------------
# REV-3 — redis-py 4.x compatible shutdown
# ---------------------------------------------------------------------------

def test_main_shutdown_redis_close_is_version_compatible():
    """redis==4.6.0 has no aclose(); an unconditional aclose() raised
    AttributeError on every shutdown and skipped close_services()."""
    with open("main.py", encoding="utf-8") as f:
        src = f.read()
    assert 'r.aclose() if hasattr(r, "aclose") else r.close()' in src
    assert "await app.state.redis.aclose()" not in src


# ---------------------------------------------------------------------------
# REV-6 — atomic upserts in mark_watched / reject_movie
# ---------------------------------------------------------------------------

def test_watched_and_reject_routes_use_atomic_upsert():
    """CONC-1 parity: no SELECT-then-branch upserts left in the two rating
    mutation routes (double-click raced the unique index into a 500)."""
    import inspect
    from routers import recommendations as recs

    for fn in (recs.mark_watched, recs.reject_movie):
        src = inspect.getsource(fn)
        assert "on_conflict_do_update" in src, f"{fn.__name__} lost its atomic upsert"
        assert "scalar_one_or_none" not in src.split("on_conflict_do_update")[1], (
            f"{fn.__name__} re-introduced a post-upsert existence check"
        )


# ---------------------------------------------------------------------------
# REV-7 — TraktClient singleton reuse
# ---------------------------------------------------------------------------

def test_recommendation_service_reuses_trakt_singleton():
    from services.recommendation_service import RecommendationService
    from services.trakt_client import get_trakt_client

    a = RecommendationService(db=Mock(), tmdb=Mock(), qdrant=Mock())
    b = RecommendationService(db=Mock(), tmdb=Mock(), qdrant=Mock())

    assert a.trakt is b.trakt
    assert a.trakt is get_trakt_client()


# ---------------------------------------------------------------------------
# REV-8 — background ingest closes its MovieService
# ---------------------------------------------------------------------------

def _fake_session_local():
    """Callable standing in for AsyncSessionLocal: returns an async CM."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("ingest_fails", [False, True])
async def test_background_ingest_always_closes_movie_service(ingest_fails):
    import services.recommendation_engine as engine

    service = Mock()
    if ingest_fails:
        service.get_or_create_movie = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        service.get_or_create_movie = AsyncMock(return_value=Mock())
    service.close = AsyncMock()

    with patch.object(engine, "MovieService", return_value=service), \
         patch("config.AsyncSessionLocal", _fake_session_local()), \
         patch("dependencies.get_tmdb_client", AsyncMock(return_value=Mock())):
        await engine._ingest_movie_background(550)

    service.get_or_create_movie.assert_awaited_once_with(550)
    # close() must run on success AND on failure (finally block).
    service.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# CLOSE-1/2/3 (2026-06-24 maintainability audit) — close/aclose hygiene
# ---------------------------------------------------------------------------

def test_all_service_clients_expose_aclose():
    """dependencies.close_services() + callers use aclose() as the canonical
    full-cleanup method. OMDb lacked it → AttributeError on every shutdown
    (CLOSE-1). Every connection client must expose aclose()."""
    from services.omdb_client import OMDbClient
    from services.tmdb_client import TMDBClient
    from services.qdrant_service import QdrantService
    from services.trakt_client import TraktClient
    from services.trending_service import TrendingService
    from services.scraper_service import ScraperService

    for cls in (OMDbClient, TMDBClient, QdrantService, TraktClient,
                TrendingService, ScraperService):
        assert callable(getattr(cls, "aclose", None)), f"{cls.__name__} missing aclose()"


def test_recommendation_service_close_does_not_close_injected_tmdb():
    """CLOSE-2: self.tmdb is the injected singleton (or None) — never owned here,
    so close() must NOT aclose it (that would kill the shared client mid-request)."""
    import inspect
    from services.recommendation_service import RecommendationService

    src = inspect.getsource(RecommendationService.close)
    assert "self.tmdb.aclose" not in src and "self.tmdb.close" not in src, (
        "RecommendationService.close() must not close the injected TMDB singleton"
    )


def test_tmdb_close_and_aclose_both_clean_up_redis():
    """CLOSE-3: close() was httpx-only (leaked Redis); it now closes Redis too,
    and aclose() delegates to it. Guard both against re-divergence."""
    import inspect
    from services.tmdb_client import TMDBClient

    close_src = inspect.getsource(TMDBClient.close)
    assert "redis_client" in close_src, "TMDBClient.close() must close Redis too"
    aclose_src = inspect.getsource(TMDBClient.aclose)
    assert "self.close()" in aclose_src, "TMDBClient.aclose() should delegate to close()"
