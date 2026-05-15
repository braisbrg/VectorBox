"""Schema and default-value tests for MovieSearchIntent (Magic Search Sprint 1).

The 5 new filter dimensions added in 2026-05-15 (mpaa_ratings, min_oscar_wins,
min_imdb_rating, min_metacritic, safe_mode) need a couple of guardrails:

  - safe_mode MUST default to True so adult content is excluded by default
    even when the LLM omits the field entirely.
  - mpaa_ratings is a list, not a string — guards against the LLM emitting
    a single string and silently downcasting / failing validation.
  - min_oscar_wins is integer; min_imdb_rating is float; min_metacritic int.

A full LLM-call test would need a live key and burn tokens; these schema-only
tests cover the bug surface that doesn't need the network.
"""
import pytest

pytest_plugins = ('pytest_asyncio',)

from services.nlp_search import MovieSearchIntent


def test_safe_mode_defaults_true():
    """No matter what the LLM emits, adult content must be excluded by
    default. Only an explicit safe_mode=False (set by the LLM when the user
    asks for NSFW) opens the gate."""
    intent = MovieSearchIntent(semantic_query="anything", reasoning="...")
    assert intent.safe_mode is True


def test_new_filters_default_to_none():
    """The other 4 new filters default to None (or 0 for booleans) so the
    apply-filters step in routers/search.py can branch on truthiness without
    accidentally constraining the query."""
    intent = MovieSearchIntent(semantic_query="thrillers", reasoning="...")
    assert intent.mpaa_ratings is None
    assert intent.min_oscar_wins is None
    assert intent.min_imdb_rating is None
    assert intent.min_metacritic is None


def test_mpaa_ratings_accepts_list():
    intent = MovieSearchIntent(
        semantic_query="family movies",
        reasoning="parsed mpaa filter",
        mpaa_ratings=["G", "PG"],
    )
    assert intent.mpaa_ratings == ["G", "PG"]


def test_min_oscar_wins_integer():
    intent = MovieSearchIntent(
        semantic_query="oscar winners",
        reasoning="...",
        min_oscar_wins=3,
    )
    assert intent.min_oscar_wins == 3


def test_min_imdb_rating_float():
    intent = MovieSearchIntent(
        semantic_query="highly rated on imdb",
        reasoning="...",
        min_imdb_rating=7.5,
    )
    assert intent.min_imdb_rating == 7.5


def test_min_metacritic_integer():
    intent = MovieSearchIntent(
        semantic_query="critically acclaimed",
        reasoning="...",
        min_metacritic=80,
    )
    assert intent.min_metacritic == 80


def test_safe_mode_can_be_disabled():
    """When the user explicitly asks for adult content, safe_mode can be
    flipped — but it must be EXPLICIT (LLM parses the request)."""
    intent = MovieSearchIntent(
        semantic_query="adult thriller",
        reasoning="user asked for adult content",
        safe_mode=False,
    )
    assert intent.safe_mode is False


def test_combined_filters_compose():
    """The new filters compose cleanly with existing ones. A realistic query
    like 'oscar-winning thrillers from the 90s' fills several dimensions."""
    intent = MovieSearchIntent(
        semantic_query="oscar-winning thrillers, suspense, noir",
        reasoning="user asked for oscar-winning 90s thrillers",
        year_min=1990,
        year_max=1999,
        include_genres=["Thriller"],
        min_oscar_wins=1,
        min_metacritic=70,
    )
    assert intent.year_min == 1990 and intent.year_max == 1999
    assert intent.include_genres == ["Thriller"]
    assert intent.min_oscar_wins == 1
    assert intent.min_metacritic == 70
    assert intent.safe_mode is True  # default preserved


# ---------- Sprint 2 filters ----------


def test_sprint2_filters_default_to_none():
    intent = MovieSearchIntent(semantic_query="anything", reasoning="...")
    assert intent.countries is None
    assert intent.spoken_languages is None
    assert intent.awards_contains is None


def test_countries_accepts_list_of_names():
    intent = MovieSearchIntent(
        semantic_query="korean cinema",
        reasoning="user asked for Korean films",
        countries=["South Korea"],
    )
    assert intent.countries == ["South Korea"]


def test_european_query_yields_multi_country_list():
    intent = MovieSearchIntent(
        semantic_query="european arthouse",
        reasoning="parser expanded 'European' to top film-producing countries",
        countries=["France", "Germany", "Spain", "Italy", "United Kingdom"],
    )
    assert len(intent.countries) == 5


def test_spoken_languages_accepts_list():
    intent = MovieSearchIntent(
        semantic_query="películas en gallego",
        reasoning="user asked for Galician-spoken films",
        spoken_languages=["Galician"],
    )
    assert intent.spoken_languages == ["Galician"]


def test_awards_contains_accepts_list_of_substrings():
    intent = MovieSearchIntent(
        semantic_query="bafta and cannes winners",
        reasoning="festival winner query",
        awards_contains=["BAFTA", "Palme"],
    )
    assert intent.awards_contains == ["BAFTA", "Palme"]


def test_sprint2_filters_compose_with_sprint1():
    """A full-fat query like 'oscar-winning Korean thrillers in Mandarin
    with BAFTA recognition' fills 5 dimensions (year, genre, countries,
    spoken_languages, oscar_wins, awards_contains, min_metacritic)."""
    intent = MovieSearchIntent(
        semantic_query="korean thrillers, suspense, noir",
        reasoning="composed multi-filter query",
        include_genres=["Thriller"],
        countries=["South Korea"],
        spoken_languages=["Korean"],
        min_oscar_wins=1,
        min_metacritic=70,
        awards_contains=["BAFTA"],
    )
    assert intent.countries == ["South Korea"]
    assert intent.spoken_languages == ["Korean"]
    assert intent.awards_contains == ["BAFTA"]
    assert intent.min_oscar_wins == 1
    # Sprint 1 defaults preserved
    assert intent.safe_mode is True
