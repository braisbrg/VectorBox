"""Signal A anchor freshness — `select_anchors` reserves slots for new watches.

Pure/synchronous: no DB, no Qdrant. Films are SimpleNamespace with a tmdb_id.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from services.clustering_service import RECENT_ANCHORS, select_anchors

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


def _film(tid):
    return SimpleNamespace(tmdb_id=tid)


# All-time favourites, best first — the set that never moved.
SCORED = [(1.5 - i * 0.1, _film(i)) for i in range(10)]


def test_recent_watches_take_reserved_slots():
    recent = [(NOW - timedelta(days=d), _film(100 + d)) for d in (1, 2, 30)]
    ids = [m.tmdb_id for m in select_anchors(SCORED, recent, 7)]

    assert len(ids) == 7
    assert ids[-RECENT_ANCHORS:] == [101, 102]  # the two newest, newest first
    assert ids[:-RECENT_ANCHORS] == [0, 1, 2, 3, 4]  # all-time top fills the rest


def test_no_recent_watches_keeps_the_old_ranking():
    assert [m.tmdb_id for m in select_anchors(SCORED, [], 7)] == [0, 1, 2, 3, 4, 5, 6]


def test_a_recent_film_never_occupies_two_slots():
    """An all-time favourite watched yesterday must not be counted twice."""
    recent = [(NOW - timedelta(days=1), SCORED[0][1])]
    ids = [m.tmdb_id for m in select_anchors(SCORED, recent, 7)]

    assert len(ids) == len(set(ids)) == 7
    assert ids == [1, 2, 3, 4, 5, 6, 0]
