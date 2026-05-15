"""VBS scoring snapshot — pins the v2 formula (Bayesian shrinkage + 3-segment
piecewise stretch + coverage factor) against representative films.

Different from `test_scoring.py` which tests *contracts* (caps, ranges, floor):
this test asserts *exact scores* for a small representative sample so that any
change to the priors, percentiles, weights, or coverage factors lights up as a
diff in CI. Numbers are tight (±0.5) — we accept tiny drift from float ops but
not a meaningful re-weighting.

If a refactor intentionally changes the formula, regenerate the expected scores:
  for case in CASES:
      result = client.calculate_vectorbox_score(...)
      print(f"{case.label}: {result.score}")
and paste the new values here as a single commit titled "vbs(formula): rev N".
"""
import pytest
from dataclasses import dataclass
from typing import Optional

from services.omdb_client import OMDbClient
from models.external_schemas import OMDbResponse


def _client() -> OMDbClient:
    return OMDbClient.__new__(OMDbClient)  # skip __init__ to avoid API key check


def _omdb(imdb_rating: Optional[float] = None, imdb_votes: Optional[int] = None, metascore: Optional[int] = None) -> OMDbResponse:
    return OMDbResponse(
        Response="True",
        imdbRating=str(imdb_rating) if imdb_rating is not None else None,
        imdbVotes=f"{imdb_votes:,}" if imdb_votes is not None else None,
        Metascore=str(metascore) if metascore is not None else None,
    )


@dataclass
class _Case:
    label: str
    imdb_rating: Optional[float]
    imdb_votes: Optional[int]
    metascore: Optional[int]
    tmdb_average: Optional[float]
    tmdb_votes: Optional[int]
    expected: float  # ±0.5


# 8 films covering the full quality + coverage spectrum.
# Source data approximated from OMDb / TMDB at the time of formula freeze (VBS v2, 2026-05).
CASES = [
    _Case("Godfather (3-source elite)",      9.2, 2_222_804, 100, 8.690,  22_852,  99.0),
    _Case("Pan's Labyrinth (3-source high)", 8.2,   720_000,  98, 7.989,   8_500,  96.2),
    _Case("Inception (3-source pop)",        8.8, 2_500_000,  74, 8.367,  37_270,  91.3),
    _Case("Whiplash (3-source mid-high)",    8.5, 1_000_000,  88, 8.4,    16_000,  96.8),
    _Case("Catalog median film",             6.7,    10_000,  60, 6.77,    1_000,  60.5),
    _Case("Sub-floor obscure film",          3.8,       450,  25, 4.2,        80,  34.1),
    _Case("Single-source TMDB-only indie",   None,      None, None, 8.497,   590,  80.4),
    _Case("Two-source partial coverage",     8.0,    50_000, None, 7.8,    5_000,  87.0),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.label)
def test_vbs_snapshot(case: _Case):
    omdb = _omdb(case.imdb_rating, case.imdb_votes, case.metascore)
    result = _client().calculate_vectorbox_score(
        omdb, case.tmdb_average, tmdb_vote_count=case.tmdb_votes, imdb_vote_count=case.imdb_votes
    )
    assert result.score is not None, f"{case.label} returned None — formula likely broken"
    drift = abs(result.score - case.expected)
    assert drift <= 0.5, (
        f"{case.label}: expected ~{case.expected}, got {result.score:.2f} "
        f"(drift {drift:.2f} > 0.5). If intentional, regenerate the snapshot."
    )
