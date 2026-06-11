"""Regression tests for the 2026-06-11 full-project review fixes:

  - REV-1  _enrich_recommendations: movies missing from the providers map
           (TMDB returned None — film has no providers anywhere) must get an
           empty provider list, not raise UnboundLocalError on the first movie
           or inherit the previous movie's providers.
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


def _db_returning_movies(movies):
    """AsyncSession stub whose execute() yields the given ORM movies."""
    result = Mock()
    result.scalars.return_value.all.return_value = movies
    db = Mock()
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# REV-1 — _enrich_recommendations provider-map gaps
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_recommendations_movie_missing_from_providers_map():
    """First movie absent from the providers map → empty list, no crash.

    Before the fix this raised UnboundLocalError (streaming_providers was
    only assigned inside `if movie.id in providers_map`), which the route's
    broad except turned into a 500 for the whole request.
    """
    from routers.recommendations import _enrich_recommendations
    from models.schemas import RecommendationRequest

    movie = _make_movie(1, 101, "Obscure Festival Film")
    db = _db_returning_movies([movie])

    provider_service = Mock()
    provider_service.get_providers_batch = AsyncMock(return_value={})  # no entry at all

    with patch("routers.recommendations.ProviderService", return_value=provider_service):
        recs = await _enrich_recommendations(
            results=[{"movie_id": 1, "score": 0.9}],
            user_id=1,
            db=db,
            request=RecommendationRequest(),  # country_code defaults to "ES"
            tmdb=Mock(),
        )

    assert len(recs) == 1
    assert recs[0].streaming_providers == []
    assert recs[0].streaming_available is False


@pytest.mark.asyncio
async def test_enrich_recommendations_no_stale_provider_carryover():
    """A movie absent from the map must NOT inherit the previous movie's
    provider list (the stale-variable variant of the same bug)."""
    from routers.recommendations import _enrich_recommendations
    from models.schemas import RecommendationRequest

    with_providers = _make_movie(1, 101, "Popular Film")
    without_providers = _make_movie(2, 102, "Obscure Film")
    db = _db_returning_movies([with_providers, without_providers])

    provider_service = Mock()
    provider_service.get_providers_batch = AsyncMock(return_value={
        1: [{"provider_id": 8, "provider_name": "Netflix"}],
        # movie 2 intentionally missing (TMDB fetch returned None)
    })

    with patch("routers.recommendations.ProviderService", return_value=provider_service):
        recs = await _enrich_recommendations(
            results=[{"movie_id": 1, "score": 0.9}, {"movie_id": 2, "score": 0.8}],
            user_id=1,
            db=db,
            request=RecommendationRequest(),
            tmdb=Mock(),
        )

    assert len(recs) == 2
    assert recs[0].streaming_providers == ["Netflix"]
    assert recs[1].streaming_providers == []  # not ["Netflix"]


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
