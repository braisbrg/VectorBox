import httpx
import os
import logging
import orjson
from typing import Optional, Dict, Any, Union
from models.external_schemas import OMDbResponse, VectorBoxScore, VectorBoxBreakdown

logger = logging.getLogger(__name__)

class OMDbClient:
    def __init__(self):
        self.api_key = os.getenv("OMDB_API_KEY")
        self.base_url = "http://www.omdbapi.com/"
        
        if not self.api_key:
            logger.warning("OMDB_API_KEY not found. VectorBox Score will be unavailable.")

        # [INFRASTRUCTURE RESILIENCE]
        limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
        timeout = httpx.Timeout(10.0, connect=3.0)
        
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            limits=limits,
            timeout=timeout,
            http2=True
        )

        # [RESILIENCE] Circuit Breaker
        self.cb_state = "CLOSED" 
        self.cb_failure_count = 0
        self.cb_threshold = 3
        self.cb_reset_timeout = 60
        self.cb_last_failure_time = 0

    async def aclose(self):
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
        current_time = asyncio.get_event_loop().time()
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
            
            response = await self.client.get("/", params=params)
            
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
        current_time = asyncio.get_event_loop().time()
        self.cb_failure_count += 1
        self.cb_last_failure_time = current_time
        
        if self.cb_failure_count >= self.cb_threshold:
            if self.cb_state != "OPEN":
                self.cb_state = "OPEN"
                logger.warning("OMDb Circuit Open. Skipping external calls.")

    def calculate_vectorbox_score(self, omdb_data: Optional[OMDbResponse], tmdb_vote_average: float) -> VectorBoxScore:
        """
        Calculate the Weighted VectorBox Score using FiveThirtyEight-style normalization.
        Return strictly typed VectorBoxScore.
        """
        scores = {}
        weights = {}
        raw_scores = {}  # Store originals for breakdown
        
        if not omdb_data:
            # Handle empty case elegantly
             # Redistribute tmdb weight effectively
             pass 

        # 1. Extract and Normalize Scores
        
        # IMDb (De-inflate)
        if omdb_data and omdb_data.imdbRating and omdb_data.imdbRating != "N/A":
            try:
                imdb_rating = float(omdb_data.imdbRating)
                raw_scores["imdb"] = imdb_rating
                scores["imdb"] = max(0, (imdb_rating - 5) * 20)  # De-inflation formula
                weights["imdb"] = 0.25
            except (ValueError, TypeError):
                pass
            
        # TMDB (De-inflate)
        if tmdb_vote_average is not None:
            raw_scores["tmdb"] = tmdb_vote_average
            scores["tmdb"] = max(0, (tmdb_vote_average - 5) * 20)  # De-inflation formula
            weights["tmdb"] = 0.25
            
        # Rotten Tomatoes (Raw 0-100)
        if omdb_data and omdb_data.Ratings:
            for rating in omdb_data.Ratings:
                if rating.Source == "Rotten Tomatoes":
                    try:
                        rt_val = int(rating.Value.replace("%", ""))
                        raw_scores["rt"] = rt_val
                        scores["rt"] = rt_val  # Already 0-100
                        weights["rt"] = 0.25
                    except (ValueError, TypeError):
                        pass
                    break
                
        # Metacritic (Raw 0-100)
        if omdb_data and omdb_data.Metascore and omdb_data.Metascore != "N/A":
            try:
                meta_val = int(omdb_data.Metascore)
                raw_scores["meta"] = meta_val
                scores["meta"] = meta_val  # Already 0-100
                weights["meta"] = 0.25
            except (ValueError, TypeError):
                pass

        # 2. Calculate Weighted Score
        total_weight = sum(weights.values())
        
        # Populate Breakdown
        breakdown = VectorBoxBreakdown(
            imdb=raw_scores.get("imdb"),
            rt=raw_scores.get("rt"),
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
            
        return VectorBoxScore(
            score=round(final_score, 1),
            breakdown=breakdown
        )
