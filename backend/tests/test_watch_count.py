"""Tests for watch_count handling in ZIP / RSS imports.

The bug fixed in 2026-05-10: RSS sync was bumping watch_count on every cron
tick (using a SQL CASE that checked the existing is_watched flag — always
true after first sync), inflating Wolf Beach, Eterna, etc. to 3 on user 212.
ZIP imports were always idempotent (use excluded.watch_count) — these tests
guard the contract going forward.

Pure-Python tests against `DataProcessor._process_diary` for the ZIP path,
and an inline simulation of the RSS upsert decision for the RSS path.
"""
import pandas as pd
import pytest

from services.data_processor import DataProcessor


def _diary_row(name, year, watched_date, rating=None, uri=None):
    return {
        "Name": name,
        "Year": year,
        "Watched Date": watched_date,
        "Date": watched_date,
        "Rating": rating,
        "Letterboxd URI": uri or f"https://letterboxd.com/film/{name.lower().replace(' ', '-')}/",
    }


def test_diary_single_entry_yields_watch_count_one():
    df = pd.DataFrame([_diary_row("Howl's Moving Castle", 2004, "2026-04-02", rating=5.0)])
    movies = {}
    DataProcessor._process_diary(df, movies)
    key = next(iter(movies))
    assert movies[key]["watch_count"] == 1


def test_diary_three_entries_same_film_yield_watch_count_three():
    """Three diary entries for the same film (rewatched twice) → watch_count=3."""
    df = pd.DataFrame([
        _diary_row("Spirited Away", 2001, "2024-01-01"),
        _diary_row("Spirited Away", 2001, "2025-06-15"),
        _diary_row("Spirited Away", 2001, "2026-02-20"),
    ])
    movies = {}
    DataProcessor._process_diary(df, movies)
    key = next(iter(movies))
    assert movies[key]["watch_count"] == 3


def test_diary_processing_is_idempotent_in_memory():
    """Re-processing the same diary df should yield the same watch_count
    (proves the in-memory pass is deterministic; the upsert layer overwrites
    watch_count with `excluded.watch_count` so re-uploading the same ZIP
    does NOT double-count)."""
    df = pd.DataFrame([
        _diary_row("Pan's Labyrinth", 2006, "2024-03-01"),
        _diary_row("Pan's Labyrinth", 2006, "2025-08-12"),
    ])
    first_pass = {}
    DataProcessor._process_diary(df, first_pass)
    second_pass = {}
    DataProcessor._process_diary(df, second_pass)
    key = next(iter(first_pass))
    assert first_pass[key]["watch_count"] == second_pass[key]["watch_count"] == 2


def test_diary_after_ratings_seed_does_not_double_count_first_entry():
    """If ratings.csv already seeded the row (counts as 1 watch), the FIRST
    diary entry should not bump — only subsequent diary entries are rewatches.
    Key format must match `_get_key` exactly: 'Title_YYYY'."""
    movies = {
        "Howl's Moving Castle_2004": {
            "title": "Howl's Moving Castle",
            "year": 2004,
            "is_watched": True,
            "watch_count": 1,
            "rating": 5.0,
            "watched_date": None,
            "letterboxd_uri": None,
        }
    }
    df = pd.DataFrame([_diary_row("Howl's Moving Castle", 2004, "2026-04-02", rating=5.0)])
    DataProcessor._process_diary(df, movies)
    # First diary entry is the same viewing as the ratings.csv seed → still 1.
    assert len(movies) == 1, "Diary processing must not create a duplicate key"
    key = next(iter(movies))
    assert movies[key]["watch_count"] == 1


def test_diary_rewatch_after_ratings_seed_increments_correctly():
    """Two diary entries on top of a ratings.csv seed → watch_count=2 (not 3)."""
    movies = {
        "Spirited Away_2001": {
            "title": "Spirited Away",
            "year": 2001,
            "is_watched": True,
            "watch_count": 1,
            "rating": 5.0,
            "watched_date": None,
            "letterboxd_uri": None,
        }
    }
    df = pd.DataFrame([
        _diary_row("Spirited Away", 2001, "2024-01-01"),
        _diary_row("Spirited Away", 2001, "2025-06-15"),
    ])
    DataProcessor._process_diary(df, movies)
    assert len(movies) == 1, "Diary processing must not create a duplicate key"
    key = next(iter(movies))
    # 1 (ratings seed) + 1 (rewatch only — first diary entry is the same viewing) = 2
    assert movies[key]["watch_count"] == 2


# ---------------- RSS bump-rule unit logic ----------------
# Replicates the Python-side condition `rewatch_flag = ...` from rss_service.py
# without spinning up a DB session. Guards the idempotency contract.


def _should_bump(rewatch: bool, incoming_date, existing_date) -> bool:
    if not rewatch:
        return False
    if existing_date is None:
        return True
    if incoming_date is None:
        return False
    return incoming_date > existing_date


def test_rss_no_rewatch_never_bumps():
    """A regular diary entry (rewatch=No) re-processed on every cron tick
    must not bump watch_count — this was the Wolf Beach bug."""
    import datetime as dt
    d = dt.datetime(2025, 9, 26)
    assert _should_bump(False, d, None) is False
    assert _should_bump(False, d, d) is False
    assert _should_bump(False, d, dt.datetime(2025, 9, 25)) is False


def test_rss_rewatch_with_new_date_bumps_once():
    """A rewatch entry with a new watched_date should bump exactly once.
    Re-processing the same entry (same date) must not bump again."""
    import datetime as dt
    new = dt.datetime(2026, 4, 1)
    old = dt.datetime(2025, 1, 15)
    assert _should_bump(True, new, old) is True   # genuine rewatch
    assert _should_bump(True, new, new) is False  # same entry re-processed
    assert _should_bump(True, old, new) is False  # backfill of older entry — never bumps


def test_rss_rewatch_no_existing_row_bumps_once():
    """First-time rewatch (no existing row): bump applies — but in practice
    the upsert path uses default watch_count=1, so this is the lower-bound case."""
    import datetime as dt
    assert _should_bump(True, dt.datetime(2026, 4, 1), None) is True
