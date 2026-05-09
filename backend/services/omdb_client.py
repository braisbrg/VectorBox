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
        Rotten Tomatoes excluded — binary consensus metric,
        prone to review bombing, not a quality signal.

        tmdb_vote_count guards against noise: under 10 votes, TMDB's average
        is one rater's opinion — we drop TMDB from the weighting rather than
        scale it as a full 0.25 contributor.
        """
        scores = {}
        weights = {}
        raw_scores = {}  # Store originals for breakdown
        
        if not omdb_data:
            # Handle empty case elegantly
             # Redistribute tmdb weight effectively
             pass 

        # 1. Extract and Normalize Scores — all sources to 0-100

        # IMDb: scale 0-10, useful range 4-10
        # Linear stretch: 4.0 → 0, 10.0 → 100
        if omdb_data and omdb_data.imdbRating \
                and omdb_data.imdbRating != "N/A":
            try:
                imdb_raw = float(omdb_data.imdbRating)
                raw_scores["imdb"] = imdb_raw
                scores["imdb"] = max(0.0, min(100.0,
                    (imdb_raw - 4.0) / 6.0 * 100
                ))
                weights["imdb"] = 0.40
            except (ValueError, TypeError):
                pass

        # TMDB: same scale as IMDb, same normalization. Skip when the best available
        # vote count is < 10 — use IMDb votes as a confidence signal when TMDB pool
        # is thin (e.g. foreign films with few TMDB ratings but many IMDb votes).
        effective_vote_count = max(tmdb_vote_count or 0, imdb_vote_count or 0)
        tmdb_has_enough_votes = effective_vote_count >= 10
        if tmdb_vote_average is not None and tmdb_has_enough_votes:
            raw_scores["tmdb"] = tmdb_vote_average
            scores["tmdb"] = max(0.0, min(100.0,
                (tmdb_vote_average - 4.0) / 6.0 * 100
            ))
            weights["tmdb"] = 0.25

        # Metacritic: already 0-100, no normalization needed
        if omdb_data and omdb_data.Metascore \
                and omdb_data.Metascore != "N/A":
            try:
                meta_val = int(omdb_data.Metascore)
                raw_scores["meta"] = meta_val
                scores["meta"] = float(meta_val)
                weights["meta"] = 0.35
            except (ValueError, TypeError):
                pass

        # 2. Calculate Weighted Score
        total_weight = sum(weights.values())
        
        # Populate Breakdown
        breakdown = VectorBoxBreakdown(
            imdb=raw_scores.get("imdb"),
            meta=raw_scores.get("meta"),
            tmdb=raw_scores.get("tmdb")
        )

        if total_weight == 0:
            return VectorBoxScore(score=None, breakdown=breakdown)
            
        # Redistribute weights proportionally and calculate
        final_score = 0
        for source, norm_score in scores.items():
            # Normalized weight = original_weight / total_weight
            final_score += norm_score * (weights[source] / total_weight)
            
        # Cap at 98 — matches the random-picks anomaly filter
        # (Movie.vectorbox_score.between(1, 98)) and prevents 100.0 outliers.
        final_score = min(final_score, 98.0)

        return VectorBoxScore(
            score=round(final_score, 1),
            breakdown=breakdown
        )
