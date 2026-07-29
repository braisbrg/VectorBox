"""Regression net for the 2026-07-25 security audit fixes.

Hermetic — no DB, no Redis, no network. Each test pins one fix so it can't
silently regress the way the pip.conf quarantine and the fail-open pip-audit
gate both did.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


# --- #1 catalogue poisoning -------------------------------------------------

def test_quality_gate_excludes_adult():
    """Any signed-in user can push a row into the shared catalogue via
    POST /movies/{id}/rate; random/wildcard serve the whole catalogue to
    everyone. Curation is not an access control — the gate must filter."""
    from services.recommendation_engine import MOVIE_QUALITY_GATE

    rendered = " ".join(str(c) for c in MOVIE_QUALITY_GATE)
    assert "is_adult" in rendered
    assert "is_excluded" in rendered


def test_ingest_rejects_adult_titles():
    """MovieService.ingest_movie must bail before any PG/Qdrant write."""
    import inspect
    from services.movie_service import MovieService

    src = inspect.getsource(MovieService.ingest_movie)
    gate = src.index("is_adult")
    assert gate < src.index("upsert_batch"), "adult gate must precede the Qdrant write"
    assert gate < src.index("self.db.add"), "adult gate must precede the PG write"


# --- #2 forgeable guest cookies ---------------------------------------------

def test_published_placeholder_is_a_weak_secret():
    """.env.example ships SECRET_KEY=your_secret_key_here and setup.sh copies
    it to .env. The old guard only knew the internal dev default, so the
    published placeholder booted fine in production."""
    from config import _WEAK_SECRETS, _ANON_SESSION_DEFAULT

    assert "your_secret_key_here" in _WEAK_SECRETS
    assert "change_me_in_prod" in _WEAK_SECRETS
    assert _ANON_SESSION_DEFAULT in _WEAK_SECRETS


# --- #8 email adoption ------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"email": "victim@example.com"},                                  # no flag at all
    {"email": "victim@example.com", "email_verified": False},
    {"email_addresses": ["victim@example.com"]},                      # bare string
    {"email_addresses": [{"email_address": "victim@example.com"}]},   # no verification block
    {"email_addresses": [{"email_address": "v@x.com",
                          "verification": {"status": "unverified"}}]},
    {},
])
def test_unverified_email_is_not_proven(payload):
    """Absence of evidence is not evidence of verification. Each of these used
    to reach the legacy-adoption branch, which binds a new Clerk identity onto
    an existing row matched by email."""
    from dependencies import _email_is_proven_verified

    assert _email_is_proven_verified(payload) is False


@pytest.mark.parametrize("payload", [
    {"email": "me@example.com", "email_verified": True},
    {"email_addresses": [{"email_address": "me@x.com",
                          "verification": {"status": "verified"}}]},
])
def test_verified_email_is_proven(payload):
    from dependencies import _email_is_proven_verified

    assert _email_is_proven_verified(payload) is True


# --- #9 ZIP input validation ------------------------------------------------

@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "1e400", "9", "-3", "abc"])
def test_zip_rating_rejects_out_of_range(raw):
    """float('inf') is accepted by Postgres `double precision`, reaches
    func.avg() in /users/me/profile, and Starlette renders with
    allow_nan=False → permanent 500 on your own profile."""
    from services.data_processor import DataProcessor

    assert DataProcessor._rating({"Rating": raw}) is None


@pytest.mark.parametrize("raw,expected", [("4.5", 4.5), (0, 0.0), (5, 5.0)])
def test_zip_rating_accepts_valid(raw, expected):
    from services.data_processor import DataProcessor

    assert DataProcessor._rating({"Rating": raw}) == expected


def test_zip_title_and_review_are_capped():
    from services.data_processor import DataProcessor

    assert len(DataProcessor._title({"Name": "x" * 9000})) == DataProcessor.MAX_TITLE
    assert len(DataProcessor._review("y" * 90000)) == DataProcessor.MAX_REVIEW


@pytest.mark.parametrize("raw", ["1799", "2101", "not-a-year", ""])
def test_zip_year_rejects_out_of_range(raw):
    from services.data_processor import DataProcessor

    assert DataProcessor._year({"Year": raw}) is None


# --- #3 ZIP import must not delete before it can rebuild --------------------

def test_upload_has_no_upfront_rating_delete():
    """The old code committed `DELETE FROM user_ratings WHERE user_id=?` in a
    BACKGROUND task before resolving a single film — a TMDB outage wiped the
    library and re-inserted nothing, after the endpoint had returned 200."""
    import inspect
    from routers.upload import enrich_movies_background

    src = inspect.getsource(enrich_movies_background)
    delete_at = src.index("sa_delete(UserRating)")
    assert "imported_movie_ids" in src
    # the only delete left is the post-import prune, guarded by the ratio check
    assert src.index("resolved_ratio") < delete_at
    assert "notin_(imported_movie_ids)" in src
    assert "watch_count != 0" in src, "web-watches must survive a ZIP import"


# --- #11 group handles ------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "a/../../etc", "user?x=1", "user#frag", "a%2Fb", "has space", "u\r\nX", "a:b", "a\\b",
])
def test_group_handle_rejects_url_structure(bad):
    from pydantic import ValidationError
    from routers.rss import GroupVibeRequest

    with pytest.raises(ValidationError):
        GroupVibeRequest(usernames=[bad, "validuser"])


@pytest.mark.parametrize("ok", ["braisbg", "guest_ab12cd34", "first.last", "a+b", "a-b"])
def test_group_handle_accepts_real_usernames(ok):
    """VB usernames are seeded from an email prefix — don't break them."""
    from routers.rss import GroupVibeRequest

    assert ok in GroupVibeRequest(usernames=[ok, "other"]).usernames


# --- #10 health disclosure --------------------------------------------------

def test_health_does_not_leak_exception_text():
    import inspect
    import main

    src = inspect.getsource(main.health_check)
    assert 'f"down: {str(e)}"' not in src
    assert '"down"' in src
