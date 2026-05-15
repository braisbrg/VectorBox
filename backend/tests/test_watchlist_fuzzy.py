"""Guards the watchlist scrape fuzzy-fallback acceptance gate.

The 2026-05-10 incident on user 212: 552-row watchlist sync inserted phantom
films because the scraper would fall back to TMDB search when the Letterboxd
film page didn't expose a tmdb_id, and the search top-1 was accepted blindly.
"Samuel and the Light" (1 vote, popularity 0.1151) landed in the user's
watchlist even though the user had never added it.

Two-tier acceptance gate (revised 2026-05-15 after audit of user 212):
  - year within ±1 of scraped year (when both available)
  - scraped title is REQUIRED — without it (legacy poster layout) bail
  - Tier A (strict): title SequenceMatcher ratio ≥ 0.95 → accept regardless
    of vote_count (covers cine galego/español indie with 1-5 votes)
  - Tier B (weak): 0.85 ≤ ratio < 0.95 → must also have vote_count ≥ 20
  - ratio < 0.85 → reject
"""
from routers.rss import _accept_fuzzy_match, _normalise_for_title_match


def _candidate(
    id=12345, title="Real Title", original_title=None, release_date="2020-01-01",
    vote_count=500, popularity=10.0,
):
    return {
        "id": id, "title": title, "original_title": original_title or title,
        "release_date": release_date, "vote_count": vote_count, "popularity": popularity,
    }


def test_accept_clean_match():
    assert _accept_fuzzy_match(
        _candidate(title="Pan's Labyrinth", release_date="2006-10-11", vote_count=8000),
        scraped_title="Pan's Labyrinth", scraped_year=2006, slug="pans-labyrinth",
    ) is True


def test_accept_title_via_original_for_foreign_film():
    """Letterboxd shows the original (Spanish) title for a Spanish film;
    TMDB's `title` may be the English translation. Must match either."""
    assert _accept_fuzzy_match(
        _candidate(title="The Devil's Backbone", original_title="El espinazo del diablo",
                   release_date="2001-04-20", vote_count=3000),
        scraped_title="El espinazo del diablo", scraped_year=2001, slug="el-espinazo-del-diablo",
    ) is True


def test_reject_low_vote_count_phantom():
    """The exact pattern that hit user 212: 1-vote film wins TMDB top-1 on
    sparse query with a wrong title — must be rejected. Combination of weak
    title ratio (≪0.85) AND low vote_count fails Tier B."""
    assert _accept_fuzzy_match(
        _candidate(title="Samuel and the Light", release_date="2023-01-01",
                   vote_count=1, popularity=0.12),
        scraped_title="Some Other Title", scraped_year=2023, slug="some-other-slug",
    ) is False


def test_accept_strict_title_match_bypasses_vote_count():
    """Tier A: legitimate ultra-indie (cine galego/español de festival) with
    very few TMDB votes. The slug matches the title near-exactly so we trust
    the resolution even though vote_count=2. Real example pattern from user
    212's watchlist (Dhogs, Samsara, Marisol llámame Pepa, etc.)."""
    assert _accept_fuzzy_match(
        _candidate(title="Dhogs", release_date="2017-09-22", vote_count=2, popularity=0.32),
        scraped_title="Dhogs", scraped_year=2017, slug="dhogs",
    ) is True


def test_reject_weak_title_match_with_low_vote_count():
    """Tier B failure: title ratio in [0.85, 0.95) but vote_count below floor.
    This is the borderline phantom case — title partially matches but not
    enough to bypass the vote-count safety net."""
    assert _accept_fuzzy_match(
        _candidate(title="A Free Park", release_date="2025-01-01", vote_count=3),
        scraped_title="A Free Man", scraped_year=2025, slug="a-free-man",
    ) is False


def test_accept_weak_title_match_with_enough_votes():
    """Tier B success: title partially matches (ratio ~0.86, in [0.85, 0.95))
    and vote_count clears the floor — the safety net allows the match."""
    assert _accept_fuzzy_match(
        _candidate(title="Avatar 2", release_date="2022-12-15", vote_count=12_000),
        scraped_title="Avatar", scraped_year=2022, slug="avatar",
    ) is True


def test_reject_year_mismatch():
    assert _accept_fuzzy_match(
        _candidate(title="Inception", release_date="2010-07-16", vote_count=30_000),
        scraped_title="Inception", scraped_year=2020, slug="inception",
    ) is False


def test_accept_year_within_tolerance():
    """±1 year drift can happen when a film is dated differently by
    festival-vs-theatrical release. Still acceptable."""
    assert _accept_fuzzy_match(
        _candidate(title="Past Lives", release_date="2023-06-02", vote_count=2000),
        scraped_title="Past Lives", scraped_year=2024, slug="past-lives",
    ) is True


def test_reject_title_drift_even_when_year_and_votes_pass():
    """Top-1 hit has good votes and matches year but the title is unrelated.
    Without the title-similarity gate this would slip through."""
    assert _accept_fuzzy_match(
        _candidate(title="The Conjuring", release_date="2013-07-19", vote_count=12_000),
        scraped_title="The Babadook", scraped_year=2014, slug="the-babadook",
    ) is False


def test_reject_when_no_scraped_title():
    """Legacy poster layout doesn't expose data-item-name. Without a scraped
    title we can't verify the fuzzy hit — must refuse rather than guess."""
    assert _accept_fuzzy_match(
        _candidate(title="The Godfather", release_date="1972-03-14", vote_count=20_000),
        scraped_title=None, scraped_year=1972, slug="the-godfather",
    ) is False


def test_normalise_for_title_match_strips_punctuation_and_lowercases():
    assert _normalise_for_title_match("It's a Wonderful Life") == _normalise_for_title_match("Its a Wonderful Life")
    assert _normalise_for_title_match("Pan's Labyrinth") == "pans labyrinth"


def test_accept_handles_apostrophes_and_case():
    assert _accept_fuzzy_match(
        _candidate(title="It's a Wonderful Life", release_date="1946-12-20", vote_count=4500),
        scraped_title="Its a Wonderful Life", scraped_year=1946, slug="its-a-wonderful-life",
    ) is True
