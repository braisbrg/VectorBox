"""An import overwrites with what it brings; what it lacks erases nothing (2026-11-10).

Found reading every ingestion path for writes that happen by timing instead of intent:

  - Two ingests of the same new film race on the unique tmdb_id. The loser's commit
    failed and `get_or_create_movie` returned None, so that user's rating for the film
    was dropped without a trace.
  - A finished ZIP import deleted every row it had not touched, including films the
    export still named but that found no match this time (a blocked Letterboxd lookup,
    a TMDB tie). One bad lookup removed a rating the user never touched. The full
    watchlist scrape demoted films the same way.
  - A metadata refresh scored the LIVE OMDb answer, so a failed OMDb call dropped the
    IMDb and Metacritic terms and wrote a TMDB-only score over a good one. Measured the
    same day: 0 of 21,506 stored scores had drifted yet, because the recalc phase puts
    them back. The first maintenance run in two months would have opened the window.

The SQL side of the same rule (the RSS and ZIP upserts) runs on Postgres in
test_rating_upserts_integration.py.
"""
from types import SimpleNamespace

import pytest

from scripts.refresh_metadata import refresh_movie
from services.import_reconcile import stale_movie_ids
from services.movie_service import MovieService
from services.omdb_client import OMDbClient


# --- the ingest race -------------------------------------------------------------------

class _Db:
    """A session whose SELECTs answer from a queue: first "not there", then the row."""

    def __init__(self, answers):
        self._answers = list(answers)

    async def execute(self, _stmt):
        value = self._answers.pop(0)
        return SimpleNamespace(scalar_one_or_none=lambda: value)


@pytest.mark.asyncio
async def test_the_loser_of_an_ingest_race_gets_the_winners_row():
    winner = SimpleNamespace(tmdb_id=1, letterboxd_uri=None)
    service = MovieService.__new__(MovieService)
    service.db = _Db([None, winner])

    async def lost_the_race(*_args, **_kwargs):
        return None  # what ingest_movie returns after the unique violation

    service.ingest_movie = lost_the_race
    assert await service.get_or_create_movie(1) is winner


# --- the ZIP prune ---------------------------------------------------------------------

ROWS = [
    (10, "Solaris", "Солярис", 1972, "https://boxd.it/2a9o"),
    (11, "The Fog", "The Fog", 1980, None),
    (12, "Heat", "Heat", 1995, "https://boxd.it/2bg8"),
]


def test_a_film_the_export_still_names_is_not_pruned():
    unresolved = [
        {"title": "Solaris", "year": 1972, "letterboxd_uri": "https://boxd.it/zzzz"},  # by title
        {"title": "Whatever It Is Called There", "year": 1995,
         "letterboxd_uri": "https://boxd.it/2bg8"},                                    # by URI
    ]
    assert stale_movie_ids(ROWS, unresolved) == [11]


def test_a_same_title_from_another_decade_does_not_protect_a_row():
    unresolved = [{"title": "The Fog", "year": 2005, "letterboxd_uri": None}]
    assert stale_movie_ids(ROWS, unresolved) == [10, 11, 12]


def test_with_nothing_unresolved_every_untouched_row_is_stale():
    assert stale_movie_ids(ROWS, []) == [10, 11, 12]


# --- the score comes from the row ------------------------------------------------------

class _TMDB:
    async def get_movie_details(self, _tmdb_id, force_refresh=False):
        return {"vote_count": 4000, "vote_average": 7.9, "popularity": 30.0, "runtime": None}


class _OMDbDown(OMDbClient):
    """The real scorer, with the network call failing the way a bad day does."""

    def __init__(self):  # no HTTP client, no settings
        pass

    async def fetch_movie_data(self, _imdb_id):
        return None


def _film():
    return SimpleNamespace(
        tmdb_id=1, imdb_id="tt0000001", title="X", vote_count=4000, vote_average=7.9,
        popularity=30.0, runtime=121, imdb_rating=8.1, imdb_vote_count=250_000,
        metacritic_rating=84, vectorbox_score=None, poster_path="/p.jpg", genres=["Drama"],
        overview="o", original_language="en", keywords=["k"], directors=["d"], cast=["c"],
        title_es=None, overview_es=None, tagline=None, backdrop_path=None, is_adult=False,
        collection_id=None, collection_name=None, mpaa_rating=None, awards_text=None,
        oscar_wins=0, omdb_countries=None, omdb_languages=None, release_dates=None,
        release_date_es=None, release_date_us=None, release_date_ww=None,
        is_upcoming=False, last_metadata_refresh=None,
    )


@pytest.mark.asyncio
async def test_a_refresh_with_omdb_down_keeps_the_full_score_and_the_runtime():
    omdb = _OMDbDown()
    film = _film()
    full = omdb.score_row(film).score
    tmdb_only = omdb.calculate_vectorbox_score(None, 7.9, tmdb_vote_count=4000).score
    assert full != tmdb_only, "the fixture must tell the two scores apart"

    assert await refresh_movie(film, _TMDB(), omdb) is True
    assert film.vectorbox_score == full
    assert film.runtime == 121, "TMDB's null runtime replaced a known one"
