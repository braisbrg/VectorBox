import pytest

pytest_plugins = ('pytest_asyncio',)

from services.omdb_client import OMDbClient
from models.external_schemas import OMDbResponse


def _client():
    return OMDbClient.__new__(OMDbClient)  # skip __init__ to avoid API key check


def _omdb(imdb_rating=None, imdb_votes=None, metascore=None):
    return OMDbResponse(
        Response="True",
        imdbRating=str(imdb_rating) if imdb_rating is not None else None,
        imdbVotes=str(imdb_votes) if imdb_votes is not None else None,
        Metascore=str(metascore) if metascore is not None else None,
    )


def test_three_sources_elite_caps_near_99():
    """Godfather-tier: IMDb 9.2, Meta 100, TMDb 8.69 — all above p99 → near 99."""
    omdb = _omdb(imdb_rating=9.2, imdb_votes=2_222_804, metascore=100)
    result = _client().calculate_vectorbox_score(omdb, 8.69, tmdb_vote_count=22_852)
    assert result.score is not None
    assert 98.0 <= result.score <= 99.0, f"Expected ~99, got {result.score}"


def test_three_sources_median_lands_around_60():
    """Catalog-median film should land in the mid-60s, not stuck at the floor."""
    # IMDb p50≈6.7, TMDb p50≈6.77, Meta p50=60
    omdb = _omdb(imdb_rating=6.7, imdb_votes=10_000, metascore=60)
    result = _client().calculate_vectorbox_score(omdb, 6.77, tmdb_vote_count=1_000)
    assert result.score is not None
    assert 55.0 <= result.score <= 65.0, f"Expected ~60, got {result.score}"


def test_single_source_tmdb_only_capped_by_coverage():
    """Selena Gomez case: TMDb 8.5 / 590 votes, no IMDb, no Meta.
    Coverage 0.85 must drag the score below ~85, even with elite TMDb."""
    omdb = _omdb()  # all None
    result = _client().calculate_vectorbox_score(
        omdb, 8.497, tmdb_vote_count=590, imdb_vote_count=None
    )
    assert result.score is not None
    assert result.score <= 85.0, f"Single-source film must not exceed 85, got {result.score}"
    assert result.score >= 70.0, f"TMDb 8.5 with coverage 0.85 should still be >70, got {result.score}"


def test_two_sources_intermediate_coverage():
    """Two sources → 0.95 coverage factor; score should sit between 1-source and 3-source."""
    omdb = _omdb(imdb_rating=8.0, imdb_votes=50_000, metascore=None)
    result = _client().calculate_vectorbox_score(omdb, 7.8, tmdb_vote_count=5_000)
    assert result.score is not None
    # IMDb 8.0 in p90..p99 region, TMDb 7.8 in p90..p99 region.
    # weighted_avg ≈ 90-94, * 0.95 ≈ 85-89
    assert 80.0 <= result.score <= 92.0, f"Expected ~85, got {result.score}"


def test_all_sources_at_floor():
    """A film at p05 across all sources with HIGH votes (shrinkage doesn't pull
    toward mean) should land at ~floor (20)."""
    omdb = _omdb(imdb_rating=5.1, imdb_votes=200_000, metascore=30)
    result = _client().calculate_vectorbox_score(omdb, 5.4, tmdb_vote_count=20_000)
    assert result.score is not None
    assert result.score <= 25.0, f"Expected ~20, got {result.score}"


def test_no_sources_returns_none():
    """No usable data → score is None, breakdown empty."""
    omdb = _omdb()
    result = _client().calculate_vectorbox_score(omdb, None)
    assert result.score is None


def test_score_never_exceeds_99():
    """Natural ceiling at 99 — even maximal inputs cannot break through."""
    omdb = _omdb(imdb_rating=10.0, imdb_votes=10_000_000, metascore=100)
    result = _client().calculate_vectorbox_score(omdb, 10.0, tmdb_vote_count=100_000)
    assert result.score is not None
    assert result.score <= 99.0, f"Score must not exceed 99, got {result.score}"


def test_breakdown_preserves_raw_values():
    """Breakdown returns the raw source values, not the stretched ones."""
    omdb = _omdb(imdb_rating=7.5, imdb_votes=10_000, metascore=70)
    result = _client().calculate_vectorbox_score(omdb, 7.2, tmdb_vote_count=1_000)
    assert result.breakdown.imdb == 7.5
    assert result.breakdown.meta == 70
    assert result.breakdown.tmdb == 7.2


def test_stretch_piecewise_breakpoints():
    """Direct check of _stretch at the four breakpoints for IMDb scale."""
    p05, p90, p99 = OMDbClient.IMDB_P05, OMDbClient.IMDB_P90, OMDbClient.IMDB_P99
    assert OMDbClient._stretch(0.0, p05, p90, p99) == 0.0
    assert OMDbClient._stretch(p05, p05, p90, p99) == 20.0
    assert OMDbClient._stretch(p90, p05, p90, p99) == 90.0
    assert OMDbClient._stretch(p99, p05, p90, p99) == 99.0
    assert abs(OMDbClient._stretch(p99 - 0.001, p05, p90, p99) - 98.0) < 0.1
    # Sub-floor segment must differentiate, not flat-floor at 20:
    assert 0.0 <= OMDbClient._stretch(p05 / 2, p05, p90, p99) < 11.0
    assert OMDbClient._stretch(p99 + 1.0, p05, p90, p99) == 99.0  # ceiling


def test_subfloor_films_differentiate():
    """Films below p05 must differentiate by quality, not collapse to floor.
    Mirror of the top-end pile-up fix."""
    # Snow White 2025-tier: IMDb 2.1, Meta 10, TMDb 4.0 — review-bombed disaster
    very_bad = _omdb(imdb_rating=2.1, imdb_votes=200_000, metascore=10)
    very_bad_score = _client().calculate_vectorbox_score(very_bad, 4.0, tmdb_vote_count=10_000)
    # A Serbian Film-tier: IMDb 4.9, Meta 20, TMDb 5.3 — bad but not absolute floor
    less_bad = _omdb(imdb_rating=4.9, imdb_votes=80_000, metascore=20)
    less_bad_score = _client().calculate_vectorbox_score(less_bad, 5.3, tmdb_vote_count=2_000)

    assert very_bad_score.score is not None and less_bad_score.score is not None
    assert very_bad_score.score < less_bad_score.score, (
        f"Sub-floor films must differentiate: very_bad={very_bad_score.score} less_bad={less_bad_score.score}"
    )
    assert very_bad_score.score < 15.0, f"Very bad film should drop below 15, got {very_bad_score.score}"
    assert 12.0 <= less_bad_score.score <= 22.0, f"Less-bad expected 12..22, got {less_bad_score.score}"
