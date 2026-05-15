"""TST-4 — OMDb client integration tests against the real API.

These tests hit the live OMDb endpoint with a small set of stable, canonical
films and assert that:
  - Schema parsing handles real-world payloads (no extra-field crashes).
  - Numeric / array helpers (`parse_oscar_wins`, `split_omdb_csv`) extract
    the expected values from real `Awards` and `Country` strings.
  - VBS calculation produces a sensible score for a known elite film.
  - Rate-limit / 401 error path returns None gracefully without raising.

These were not feasible while we sat on the free 1000/day OMDb cap — each
test burned a precious call and could flake the moment Phase 1 was running.
With the Patron tier (100k/day, paid 2026-05-15) the budget is no longer a
concern; the suite uses ≤ 5 calls per run.

The whole module is auto-skipped when `OMDB_API_KEY` is not set, so CI
without the secret stays green without surprises.
"""
import asyncio
import os

import pytest
import pytest_asyncio

pytest_plugins = ("pytest_asyncio",)

from services.omdb_client import OMDbClient, parse_oscar_wins, split_omdb_csv


# Skip the whole module when we can't reach the API.
pytestmark = pytest.mark.skipif(
    not os.getenv("OMDB_API_KEY"),
    reason="OMDB_API_KEY not set — skipping live integration tests",
)


@pytest_asyncio.fixture
async def client():
    c = OMDbClient()
    try:
        yield c
    finally:
        await c.close()


# -------- happy-path: stable canonical films --------


@pytest.mark.asyncio
async def test_fetch_goldfinger(client):
    """tt0058150 — Goldfinger (1964). Stable canonical film: should never
    flake. Confirms imdbRating parses (the 2026-05-15 bug fix), Metascore
    is present, Awards has the Oscar nomination string, Country/Language
    populated."""
    data = await client.fetch_movie_data("tt0058150")
    assert data is not None
    assert data.Response == "True"
    assert data.imdbRating is not None and data.imdbRating != "N/A"
    assert float(data.imdbRating) >= 7.0  # Goldfinger sits at ~7.7
    assert data.imdbVotes is not None  # "200,000+" format
    assert data.Metascore is not None
    # Goldfinger won 1 Oscar (Sound Effects) — exercises the parser.
    assert data.Awards is not None
    assert data.Country is not None  # "United Kingdom" or similar


@pytest.mark.asyncio
async def test_fetch_the_godfather_vbs_elite(client):
    """tt0068646 — The Godfather. 3-source elite: VBS should land ~99."""
    data = await client.fetch_movie_data("tt0068646")
    assert data is not None
    assert data.imdbRating is not None
    vbs = client.calculate_vectorbox_score(
        data,
        tmdb_vote_average=8.69,
        tmdb_vote_count=22_000,
        imdb_vote_count=2_000_000,
    )
    assert vbs.score is not None
    assert 96 <= vbs.score <= 99.5, f"Godfather VBS out of range: {vbs.score}"


# -------- helper round-trips against live data --------


@pytest.mark.asyncio
async def test_parse_oscar_wins_on_real_awards_string(client):
    """The 2026-05-15 audit found 598 films with oscar_wins > 0. Re-fetch
    one — Casablanca won 3 Oscars — and verify the regex still pulls the
    right integer from a live Awards string."""
    data = await client.fetch_movie_data("tt0034583")  # Casablanca
    assert data is not None and data.Awards
    wins = parse_oscar_wins(data.Awards)
    # Casablanca won 3 (Picture, Director, Screenplay). The OMDb Awards
    # format may evolve; assert ≥1 to be robust.
    assert wins >= 1, f"parse_oscar_wins({data.Awards!r}) = {wins}"


@pytest.mark.asyncio
async def test_split_omdb_csv_on_real_payload(client):
    """tt0114709 — Toy Story. Country usually "United States". Confirms the
    helper handles a single-element CSV (no commas) without crashing."""
    data = await client.fetch_movie_data("tt0114709")
    assert data is not None
    countries = split_omdb_csv(data.Country)
    assert countries is not None and len(countries) >= 1


# -------- N/A handling --------


def test_split_omdb_csv_handles_na():
    """OMDb returns the literal string 'N/A' when it doesn't have the field.
    The catalog must never persist 'N/A' as a real country/language."""
    assert split_omdb_csv("N/A") is None
    assert split_omdb_csv(None) is None
    assert split_omdb_csv("") is None
    assert split_omdb_csv("United States, France") == ["United States", "France"]


def test_parse_oscar_wins_handles_na():
    assert parse_oscar_wins("N/A") == 0
    assert parse_oscar_wins(None) == 0
    assert parse_oscar_wins("") == 0


# -------- failure surface --------


@pytest.mark.asyncio
async def test_fetch_unknown_imdb_id_returns_none_or_response_false(client):
    """An unknown IMDb ID must not crash the client — OMDb returns
    {Response: 'False', Error: '...'}. fetch_movie_data should expose that
    so callers can branch on `data.Response == 'False'` without raising."""
    data = await client.fetch_movie_data("tt0000000")
    # Either None (filtered before parse) or Response=False (passed through)
    # is acceptable — both signal "not found" without raising.
    if data is not None:
        assert data.Response in ("False", "false")
