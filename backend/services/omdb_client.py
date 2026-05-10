import httpx
import os
import logging
import orjson
from typing import Optional, Dict, Any, Union
from models.external_schemas import OMDbResponse, VectorBoxScore, VectorBoxBreakdown

logger = logging.getLogger(__name__)

class OMDbClient:
    def __init__(self, api_key: Optional[str] = None, client: httpx.AsyncClient = None):
        self.api_key = api_key or os.getenv("OMDB_API_KEY")
        self.base_url = "http://www.omdbapi.com/"
        self._external_client = client
        self.client = client if client else httpx.AsyncClient(timeout=10.0)
        
        # [RESILIENCE] Circuit Breaker
        self.cb_state = "CLOSED" 
        self.cb_failure_count = 0
        self.cb_threshold = 3
        self.cb_reset_timeout = 60
        self.cb_last_failure_time = 0

    async def close(self):
        if not self._external_client:
            await self.client.aclose()

    async def fetch_movie_data(self, imdb_id: str) -> Optional[OMDbResponse]:
        """
        Fetch movie data from OMDb by IMDb ID using connection pool.
        Returns Pydantic model OMDbResponse or None.
        """
        if not self.api_key or not imdb_id:
            return None

        # [RESILIENCE] Circuit Breaker Check
        import asyncio
        current_time = asyncio.get_running_loop().time()
        if self.cb_state == "OPEN":
            if current_time - self.cb_last_failure_time > self.cb_reset_timeout:
                logger.info("OMDb Circuit Breaker entering HALF-OPEN state.")
                self.cb_state = "HALF-OPEN"
            else:
                return None

        try:
            # [ZERO-LEAK] Params passed per request
            params={
                "apikey": self.api_key,
                "i": imdb_id,
                "plot": "short",
                "r": "json"
            }
            
            response = await self.client.get(self.base_url, params=params)
            
            if response.status_code == 200:
                # Success - Reset Circuit Breaker
                if self.cb_state != "CLOSED":
                    logger.info("OMDb Circuit Breaker recovered (CLOSED).")
                    self.cb_state = "CLOSED"
                    self.cb_failure_count = 0

                # [PERFORMANCE] orjson
                data = orjson.loads(response.content)
                if data.get("Response") == "True":
                    # Validate with Pydantic
                    return OMDbResponse(**data)
                else:
                    logger.warning(f"OMDb Error for {imdb_id}: {data.get('Error')}")
            else:
                logger.error(f"OMDb HTTP Error {response.status_code} for {imdb_id}")
                if response.status_code >= 500:
                    self._record_failure()
                    
        except Exception as e:
            logger.error(f"Error fetching OMDb data for {imdb_id}: {e}")
            self._record_failure()
            
        return None

    def _record_failure(self):
        """Record a failure and optionally open the circuit"""
        import asyncio
        current_time = asyncio.get_running_loop().time()
        self.cb_failure_count += 1
        self.cb_last_failure_time = current_time
        
        if self.cb_failure_count >= self.cb_threshold:
            if self.cb_state != "OPEN":
                self.cb_state = "OPEN"
                logger.warning("OMDb Circuit Open. Skipping external calls.")

    # Calibration constants — derived from actual catalog distribution (n≈7400).
    # Three-segment piecewise linear stretch (symmetric):
    #   0 ≤ raw < p05    → linear 0..20 (sub-floor differentiation)
    #   p05 ≤ raw ≤ p90  → linear 20..90 (broad meaningful range)
    #   p90 < raw ≤ p99  → linear 90..98 (compressed, diminishing returns)
    #   raw > p99         → STRETCH_CEIL (99) — natural ceiling, no hard cap
    #
    # The compressed top segment prevents flat-ceiling pile-up; the linear
    # sub-floor segment prevents the equivalent flat-floor at 20 (where the
    # worst-of-the-worst — Snow White 2025, Batman & Robin, Madame Web —
    # collided indistinguishably).
    STRETCH_FLOOR = 20.0
    STRETCH_CEIL = 99.0

    # Per-source p05 / p90 / p99 from catalog (data-driven, computed from BD)
    IMDB_P05, IMDB_P90, IMDB_P99 = 5.1, 7.8, 8.4
    TMDB_P05, TMDB_P90, TMDB_P99 = 5.4, 7.7, 8.28
    META_P05, META_P90, META_P99 = 30.0, 84.0, 96.0

    # Coverage factor by number of present sources (1-3). Penalises thin-data
    # films that lack cross-source validation, e.g. a TMDb-only documentary
    # used to ride a single high vote_average all the way to the cap.
    COVERAGE_FACTORS = {1: 0.85, 2: 0.95, 3: 1.00}

    # Bayesian shrinkage priors. The 'm' parameter is the prior strength
    # (catalog-mean votes equivalent); larger m pulls low-vote movies harder
    # toward the global mean C.
    IMDB_PRIOR_M = 2000   # IMDb has wide vote distribution (50 to millions)
    IMDB_PRIOR_C = 6.69   # Catalog mean IMDb rating
    TMDB_PRIOR_M = 200    # TMDB has narrower distribution (10 to ~5000)
    TMDB_PRIOR_C = 6.67   # Catalog mean TMDB vote_average

    @staticmethod
    def _bayesian_shrink(rating: float, votes: Optional[int], m: int, C: float) -> float:
        """Pull a rating toward catalog mean C based on vote-count confidence.
        Falls through unchanged when votes is None (caller did not provide)."""
        if votes is None or votes <= 0:
            return rating
        return (votes / (votes + m)) * rating + (m / (votes + m)) * C

    @classmethod
    def _stretch(cls, raw: float, p05: float, p90: float, p99: float) -> float:
        """Three-segment piecewise linear stretch.
        0..p05 → 0..20 (sub-floor), p05..p90 → 20..90, p90..p99 → 90..98, >p99 → 99."""
        if raw <= 0:
            return 0.0
        if raw < p05:
            return (raw / p05) * cls.STRETCH_FLOOR
        if raw <= p90:
            norm = (raw - p05) / (p90 - p05)
            return cls.STRETCH_FLOOR + norm * (90.0 - cls.STRETCH_FLOOR)
        if raw >= p99:
            return cls.STRETCH_CEIL
        norm = (raw - p90) / (p99 - p90)
        return 90.0 + norm * 8.0

    def calculate_vectorbox_score(
        self,
        omdb_data: Optional[OMDbResponse],
        tmdb_vote_average: float,
        tmdb_vote_count: Optional[int] = None,
        imdb_vote_count: Optional[int] = None,
    ) -> VectorBoxScore:
        """
        Calculates VectorBox score from three sources:
        IMDb (40%), Metacritic (35%), TMDB (25%).
        Rotten Tomatoes excluded — binary consensus metric.

        Pipeline per source:
          1. Bayesian shrinkage (IMDb, TMDB) — pulls low-vote ratings toward
             the catalog mean. Metacritic skipped: it's curated journalism,
             not crowd-sourced, so vote_count doesn't apply.
          2. Two-segment piecewise stretch — p05..p90 → 20..90 (broad),
             p90..p99 → 90..98 (compressed), >p99 → 99 (natural ceiling).
          3. Coverage factor — multiplies final score by 0.85/0.95/1.00 for
             1/2/3 sources present, penalising thin-data films.

        Vote-count gate: TMDB still requires effective_vote_count >= 10 to
        enter at all. IMDb votes act as a cross-source confidence signal.
        """
        scores = {}
        weights = {}
        raw_scores = {}

        # 1. IMDb — Bayesian shrink + piecewise stretch
        if omdb_data and omdb_data.imdbRating and omdb_data.imdbRating != "N/A":
            try:
                imdb_raw = float(omdb_data.imdbRating)
                raw_scores["imdb"] = imdb_raw
                shrunk = self._bayesian_shrink(
                    imdb_raw, imdb_vote_count, self.IMDB_PRIOR_M, self.IMDB_PRIOR_C
                )
                scores["imdb"] = self._stretch(
                    shrunk, self.IMDB_P05, self.IMDB_P90, self.IMDB_P99
                )
                weights["imdb"] = 0.40
            except (ValueError, TypeError):
                pass

        # 2. TMDB — same pipeline + vote-count gate.
        # F-16 cross-source rescue: thin TMDB pool (<10 votes) is OK if IMDb is robust,
        # but TMDB MUST have at least 1 real vote — otherwise vote_average is a phantom
        # placeholder, not a signal.
        effective_vote_count = max(tmdb_vote_count or 0, imdb_vote_count or 0)
        tmdb_has_enough_votes = effective_vote_count >= 10 and (tmdb_vote_count or 0) >= 1
        if tmdb_vote_average is not None and tmdb_has_enough_votes:
            raw_scores["tmdb"] = tmdb_vote_average
            shrunk = self._bayesian_shrink(
                tmdb_vote_average, tmdb_vote_count, self.TMDB_PRIOR_M, self.TMDB_PRIOR_C
            )
            scores["tmdb"] = self._stretch(
                shrunk, self.TMDB_P05, self.TMDB_P90, self.TMDB_P99
            )
            weights["tmdb"] = 0.25

        # 3. Metacritic — stretch only (no shrinkage; not crowd-sourced)
        if omdb_data and omdb_data.Metascore and omdb_data.Metascore != "N/A":
            try:
                meta_val = int(omdb_data.Metascore)
                raw_scores["meta"] = meta_val
                scores["meta"] = self._stretch(
                    float(meta_val), self.META_P05, self.META_P90, self.META_P99
                )
                weights["meta"] = 0.35
            except (ValueError, TypeError):
                pass

        # 4. Weighted aggregate with proportional redistribution
        total_weight = sum(weights.values())

        breakdown = VectorBoxBreakdown(
            imdb=raw_scores.get("imdb"),
            meta=raw_scores.get("meta"),
            tmdb=raw_scores.get("tmdb"),
        )

        if total_weight == 0:
            return VectorBoxScore(score=None, breakdown=breakdown)

        weighted_avg = sum(
            norm * (weights[src] / total_weight) for src, norm in scores.items()
        )

        # 5. Coverage penalty — single-source films can't ride one inflated
        # rating to the top without cross-source validation.
        coverage = self.COVERAGE_FACTORS.get(len(scores), 0.85)
        final_score = weighted_avg * coverage

        return VectorBoxScore(score=round(final_score, 1), breakdown=breakdown)
