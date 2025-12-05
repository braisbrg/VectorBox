import httpx
import os
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class OMDbClient:
    def __init__(self):
        self.api_key = os.getenv("OMDB_API_KEY")
        self.base_url = "http://www.omdbapi.com/"
        
        if not self.api_key:
            logger.warning("OMDB_API_KEY not found. VectorBox Score will be unavailable.")

    async def fetch_movie_data(self, imdb_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch movie data from OMDb by IMDb ID.
        """
        if not self.api_key or not imdb_id:
            return None

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.base_url,
                    params={
                        "apikey": self.api_key,
                        "i": imdb_id,
                        "plot": "short",
                        "r": "json"
                    },
                    timeout=5.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get("Response") == "True":
                        return data
                    else:
                        logger.warning(f"OMDb Error for {imdb_id}: {data.get('Error')}")
                else:
                    logger.error(f"OMDb HTTP Error {response.status_code} for {imdb_id}")
                    
        except Exception as e:
            logger.error(f"Error fetching OMDb data for {imdb_id}: {e}")
            
        return None

    def calculate_vectorbox_score(self, omdb_data: Dict[str, Any], tmdb_vote_average: float) -> Dict[str, Any]:
        """
        Calculate the Weighted VectorBox Score.
        
        Formula:
        - imdb_norm = imdb_rating * 10
        - tmdb_norm = tmdb_vote_average * 10
        - rt_norm = rotten_tomatoes_rating (0-100)
        - meta_norm = metacritic_rating (0-100)
        
        Weights: 0.25 each. Redistribute if missing.
        """
        scores = {}
        weights = {}
        
        # 1. Extract and Normalize Scores
        
        # IMDb
        try:
            imdb_rating = float(omdb_data.get("imdbRating", "N/A"))
            scores["imdb"] = imdb_rating * 10
            weights["imdb"] = 0.25
        except (ValueError, TypeError):
            pass
            
        # TMDB
        if tmdb_vote_average is not None:
            scores["tmdb"] = tmdb_vote_average * 10
            weights["tmdb"] = 0.25
            
        # Rotten Tomatoes
        for rating in omdb_data.get("Ratings", []):
            if rating["Source"] == "Rotten Tomatoes":
                try:
                    rt_val = int(rating["Value"].replace("%", ""))
                    scores["rt"] = rt_val
                    weights["rt"] = 0.25
                except (ValueError, TypeError):
                    pass
                break
                
        # Metacritic
        try:
            meta_val = int(omdb_data.get("Metascore", "N/A"))
            scores["meta"] = meta_val
            weights["meta"] = 0.25
        except (ValueError, TypeError):
            pass

        # 2. Calculate Weighted Score
        total_weight = sum(weights.values())
        
        if total_weight == 0:
            return {
                "score": None,
                "breakdown": {
                    "imdb": scores.get("imdb"),
                    "rt": scores.get("rt"),
                    "meta": scores.get("meta"),
                    "tmdb": scores.get("tmdb")
                }
            }
            
        # Redistribute weights
        final_score = 0
        for source, score in scores.items():
            # Normalized weight = original_weight / total_weight
            # Contribution = score * normalized_weight
            final_score += score * (weights[source] / total_weight)
            
        return {
            "score": round(final_score, 1),
            "breakdown": {
                "imdb": scores.get("imdb") / 10 if "imdb" in scores else None, # Return original scale
                "rt": scores.get("rt"),
                "meta": scores.get("meta"),
                "tmdb": scores.get("tmdb") / 10 if "tmdb" in scores else None # Return original scale
            }
        }
