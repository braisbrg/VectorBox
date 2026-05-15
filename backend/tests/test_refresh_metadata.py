"""Regression tests for refresh_metadata.refresh_movie persistence.

The 2026-05-15 bug: refresh_movie called OMDb successfully, updated
`last_metadata_refresh`, but silently skipped writing `imdb_rating` and
`metacritic_rating`. ~552 catalog films had imdb_rating IS NULL despite
recent refresh timestamps.

These tests pin the contract going forward: every field the TMDB / OMDb
responses can carry MUST land in the corresponding Movie column. New
columns added to refresh_movie (or to the OMDb / TMDB response schemas)
should add an assertion here.

Pure-Python: mocks the two API clients with deterministic payloads —
no network, no real DB writes (mutates a plain Movie instance in memory).
"""
import pytest

pytest_plugins = ('pytest_asyncio',)

from datetime import date
from types import SimpleNamespace

from models.database import Movie
from models.external_schemas import OMDbResponse
from scripts.refresh_metadata import refresh_movie, _parse_oscar_wins


class _StubTMDB:
    """Returns a fixed payload from get_movie_details, ignores tmdb_id."""

    def __init__(self, payload):
        self._payload = payload

    async def get_movie_details(self, tmdb_id, force_refresh=False):
        return self._payload


class _StubOMDb:
    """Returns a fixed OMDbResponse and a vectorbox score from calculate_vectorbox_score."""

    def __init__(self, response, vb_score=72.5):
        self._response = response
        self._vb_score = vb_score

    async def fetch_movie_data(self, imdb_id):
        return self._response

    def calculate_vectorbox_score(
        self, omdb_data, tmdb_vote_average, tmdb_vote_count=None, imdb_vote_count=None
    ):
        return SimpleNamespace(score=self._vb_score)


def _full_tmdb_payload():
    return {
        "vote_count": 2_345_678,
        "vote_average": 8.4,
        "popularity": 312.5,
        "poster_path": "/poster.jpg",
        "genres": [{"name": "Drama"}, {"name": "Crime"}],
        "runtime": 175,
        "overview": "Don Vito Corleone, head of a mafia family...",
        "original_language": "en",
        "keywords_flat": ["mafia", "family", "loyalty"],
        "directors": ["Francis Ford Coppola"],
        "cast": ["Marlon Brando", "Al Pacino", "James Caan"],
        "title_es": "El Padrino",
        "overview_es": "Don Vito Corleone, jefe de una familia mafiosa...",
        "imdb_id": "tt0068646",
        "tagline": "An offer you can't refuse.",
        "backdrop_path": "/backdrop.jpg",
        "adult": False,
        "belongs_to_collection": {"id": 230, "name": "The Godfather Collection"},
    }


def _full_omdb_response():
    return OMDbResponse(
        Response="True",
        imdbRating="9.2",
        imdbVotes="2,222,804",
        Metascore="100",
        Rated="R",
        Awards="Won 3 Oscars. 33 wins & 41 nominations total",
        Country="United States",
        Language="English, Italian, Latin",
    )


def _make_movie(**overrides):
    """Build a Movie instance with all the columns set to None / defaults so
    we can detect what refresh_movie writes."""
    m = Movie(
        id=1,
        tmdb_id=238,
        imdb_id="tt0068646",
        title="The Godfather",
        year=1972,
    )
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


# ---------- happy path ----------


@pytest.mark.asyncio
async def test_refresh_persists_all_omdb_fields():
    """Regression for 2026-05-15 bug: imdb_rating + metacritic_rating + imdb_vote_count
    must all populate after a successful OMDb call."""
    movie = _make_movie()
    tmdb = _StubTMDB(_full_tmdb_payload())
    omdb = _StubOMDb(_full_omdb_response())

    ok = await refresh_movie(movie, tmdb, omdb)

    assert ok is True
    assert movie.imdb_rating == 9.2
    assert movie.metacritic_rating == 100
    assert movie.imdb_vote_count == 2_222_804
    assert movie.last_metadata_refresh is not None


@pytest.mark.asyncio
async def test_refresh_persists_all_omdb_extended_fields():
    """OMDb Rated / Awards / Country / Language (added 2026-05-15 migration o3p4q5r6s7t8)."""
    movie = _make_movie()
    tmdb = _StubTMDB(_full_tmdb_payload())
    omdb = _StubOMDb(_full_omdb_response())

    await refresh_movie(movie, tmdb, omdb)

    assert movie.mpaa_rating == "R"
    assert movie.awards_text == "Won 3 Oscars. 33 wins & 41 nominations total"
    assert movie.oscar_wins == 3
    assert movie.omdb_countries == ["United States"]
    assert movie.omdb_languages == ["English", "Italian", "Latin"]


@pytest.mark.asyncio
async def test_refresh_persists_all_tmdb_fields():
    """Every field TMDB.get_movie_details returns must land in the Movie row."""
    movie = _make_movie()
    tmdb = _StubTMDB(_full_tmdb_payload())
    omdb = _StubOMDb(_full_omdb_response())

    await refresh_movie(movie, tmdb, omdb)

    assert movie.vote_count == 2_345_678
    assert movie.vote_average == 8.4
    assert movie.popularity == 312.5
    assert movie.poster_path == "/poster.jpg"
    assert movie.genres == ["Drama", "Crime"]
    assert movie.runtime == 175
    assert movie.overview.startswith("Don Vito Corleone")
    assert movie.original_language == "en"
    assert movie.keywords == ["mafia", "family", "loyalty"]
    assert movie.directors == ["Francis Ford Coppola"]
    assert movie.cast == ["Marlon Brando", "Al Pacino", "James Caan"]
    assert movie.title_es == "El Padrino"
    assert movie.overview_es.startswith("Don Vito Corleone, jefe")
    assert movie.tagline == "An offer you can't refuse."
    assert movie.backdrop_path == "/backdrop.jpg"
    assert movie.is_adult is False
    assert movie.collection_id == 230
    assert movie.collection_name == "The Godfather Collection"
    assert movie.vectorbox_score == 72.5


# ---------- N/A handling (must not corrupt with the literal "N/A" string) ----------


@pytest.mark.asyncio
async def test_refresh_skips_na_omdb_fields():
    """OMDb returns "N/A" for missing data — never persist that as a real value.
    Existing values must also be preserved (don't downgrade a row that had data)."""
    movie = _make_movie(oscar_wins=4)  # row already had 4 Oscars
    tmdb = _StubTMDB(_full_tmdb_payload())
    omdb = _StubOMDb(
        OMDbResponse(
            Response="True",
            imdbRating="N/A",
            imdbVotes="N/A",
            Metascore="N/A",
            Rated="N/A",
            Awards="N/A",
            Country="N/A",
            Language="N/A",
        )
    )

    await refresh_movie(movie, tmdb, omdb)

    assert movie.imdb_rating is None
    assert movie.metacritic_rating is None
    assert movie.imdb_vote_count is None
    assert movie.mpaa_rating is None
    assert movie.awards_text is None
    assert movie.oscar_wins == 4  # preserved — not overwritten with 0
    assert movie.omdb_countries is None
    assert movie.omdb_languages is None


@pytest.mark.asyncio
async def test_refresh_preserves_existing_values_when_payload_lacks_keys():
    """If TMDB doesn't return a field, the existing Movie value must be kept,
    not overwritten with None. Guards against partial responses wiping data."""
    movie = _make_movie(
        vote_count=10_000,
        vote_average=7.5,
        popularity=99.0,
        poster_path="/old.jpg",
        runtime=120,
        overview="Old overview",
        original_language="ja",
    )
    sparse_payload = {}  # TMDB returned almost nothing
    tmdb = _StubTMDB(sparse_payload)
    omdb = _StubOMDb(OMDbResponse(Response="False", Error="movie not found"))

    await refresh_movie(movie, tmdb, omdb)

    assert movie.vote_count == 10_000
    assert movie.vote_average == 7.5
    assert movie.popularity == 99.0
    assert movie.poster_path == "/old.jpg"
    assert movie.runtime == 120
    assert movie.overview == "Old overview"
    assert movie.original_language == "ja"


# ---------- helper ----------


def test_parse_oscar_wins_recognises_won_n_oscars():
    assert _parse_oscar_wins("Won 11 Oscars. 33 wins & 41 nominations total") == 11
    assert _parse_oscar_wins("Won 3 Oscars") == 3


def test_parse_oscar_wins_returns_zero_for_nominations_only():
    assert _parse_oscar_wins("Nominated for 3 BAFTA Film Awards. 5 wins & 12 nominations total") == 0
    assert _parse_oscar_wins("1 win & 5 nominations total") == 0


def test_parse_oscar_wins_empty_and_na():
    assert _parse_oscar_wins("") == 0
    assert _parse_oscar_wins(None) == 0


# ---------- imdb_id backfill ----------


@pytest.mark.asyncio
async def test_refresh_backfills_missing_imdb_id_from_tmdb():
    """When a movie was ingested without imdb_id, TMDB's get_movie_details
    can supply it. refresh_movie must adopt it (and then the subsequent
    OMDb call uses it)."""
    movie = _make_movie(imdb_id=None)
    payload = _full_tmdb_payload()
    payload["imdb_id"] = "tt9999999"
    tmdb = _StubTMDB(payload)
    omdb = _StubOMDb(_full_omdb_response())

    await refresh_movie(movie, tmdb, omdb)

    assert movie.imdb_id == "tt9999999"
