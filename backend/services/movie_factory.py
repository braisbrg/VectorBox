import asyncio
import logging
from typing import Optional, Tuple, Dict, Any, List
from datetime import datetime

from models.database import Movie
from services.tmdb_client import TMDBClient
from services.omdb_client import OMDbClient, parse_oscar_wins, split_omdb_csv
from services.embedding_service import EmbeddingService
from services.cinematic_enricher import generate_cinematic_description
from qdrant_client.models import PointStruct

logger = logging.getLogger(__name__)

class MovieFactory:
    """
    Centralized factory for creating Movie objects and their corresponding Vector Points.
    Unifies logic from seed_db.py and movie_service.py.
    """

    def __init__(self, tmdb_client: TMDBClient, omdb_client: OMDbClient, embedding_service: EmbeddingService, groq_client=None):
        self.tmdb = tmdb_client
        self.omdb = omdb_client
        self.embedding_service = embedding_service
        self.groq_client = groq_client

    async def build_movie(self, tmdb_id: int, letterboxd_uri: Optional[str] = None) -> Tuple[Optional[Movie], Optional[PointStruct]]:
        """
        Orchestrates the full pipeline:
        1. Fetch TMDB Details
        2. Fetch/Calculate OMDb Data (VectorBox Score)
        3. Parse Release Dates
        4. Construct SQL Model
        5. Generate Embedding
        6. Construct Qdrant Point
        
        Returns: (Movie, PointStruct) or (None, None) if failed.
        """
        try:
            # 1. Fetch TMDB Details
            details = await self.tmdb.get_movie_details(tmdb_id)
            if not details:
                logger.warning(f"TMDB ID {tmdb_id} not found.")
                return None, None

            # 2. Fetch OMDb Data (VectorBox Score)
            imdb_id = details.get("imdb_id")
            omdb_data = None
            if imdb_id:
                omdb_data = await self.omdb.fetch_movie_data(imdb_id)
            
            # Parse IMDb vote count from OMDb response (format: "1,234,567")
            imdb_vote_count = None
            if omdb_data and omdb_data.imdbVotes:
                raw = omdb_data.imdbVotes.replace(",", "").strip()
                if raw.isdigit():
                    imdb_vote_count = int(raw)

            # Returns VectorBoxScore object
            vb_score_data = self.omdb.calculate_vectorbox_score(
                omdb_data,
                details.get("vote_average"),
                tmdb_vote_count=details.get("vote_count"),
                imdb_vote_count=imdb_vote_count,
            )

            # Fallback: TMDB-only score if OMDb unavailable and vote pool is trustworthy.
            vectorbox_score = vb_score_data.score
            if (
                not vectorbox_score
                and details.get("vote_average")
                and (details.get("vote_count") or 0) >= 10
            ):
                vectorbox_score = round((details["vote_average"] / 10) * 100 * 0.6, 1)

            # 3. Process Release Dates
            release_dates_map = self._process_release_dates(details)

            # OMDb extended metadata (Rated/Awards/Country/Language) — silently
            # skipped before; kept symmetrical with refresh_metadata.refresh_movie
            # so a re-ingest of an existing tmdb_id never *drops* a field that the
            # refresh script would persist.
            mpaa_rating = None
            awards_text = None
            oscar_wins = 0
            omdb_countries = None
            omdb_languages = None
            if omdb_data:
                if omdb_data.Rated and omdb_data.Rated != "N/A":
                    mpaa_rating = omdb_data.Rated
                if omdb_data.Awards and omdb_data.Awards != "N/A":
                    awards_text = omdb_data.Awards
                    oscar_wins = parse_oscar_wins(omdb_data.Awards)
                omdb_countries = split_omdb_csv(omdb_data.Country)
                omdb_languages = split_omdb_csv(omdb_data.Language)

            collection = details.get("belongs_to_collection") or {}

            # 4. Construct Movie Object (SQL)
            movie = Movie(
                tmdb_id=tmdb_id,
                title=details.get("title"),
                original_title=details.get("original_title"),
                year=int(details.get("release_date", "0000")[:4]) if details.get("release_date") else None,
                runtime=details.get("runtime"),
                genres=[g["name"] for g in details.get("genres", [])],
                overview=details.get("overview"),
                poster_path=details.get("poster_path"),
                backdrop_path=details.get("backdrop_path"),
                tagline=details.get("tagline") or None,
                is_adult=bool(details.get("adult", False)),
                vote_average=details.get("vote_average"),
                vote_count=details.get("vote_count"),
                popularity=details.get("popularity"),
                original_language=details.get("original_language"),
                letterboxd_uri=letterboxd_uri or f"https://letterboxd.com/tmdb/{tmdb_id}",

                # Extended Fields
                imdb_id=imdb_id,
                imdb_vote_count=imdb_vote_count,
                imdb_rating=vb_score_data.breakdown.imdb,
                metacritic_rating=vb_score_data.breakdown.meta,
                vectorbox_score=vectorbox_score,
                title_es=details.get("title_es"),
                overview_es=details.get("overview_es"),
                collection_id=collection.get("id"),
                collection_name=collection.get("name"),
                keywords=details.get("keywords_flat", []),
                directors=details.get("directors", []),
                cast=details.get("cast", []),
                release_dates=release_dates_map,

                # OMDb extended (migration o3p4q5r6s7t8)
                mpaa_rating=mpaa_rating,
                awards_text=awards_text,
                oscar_wins=oscar_wins,
                omdb_countries=omdb_countries,
                omdb_languages=omdb_languages,
            )

            # 5. Generate Embedding
            # Try LLM-enriched cinematic description first
            keywords = movie.keywords or []
            text_override = None
            
            if self.groq_client:
                try:
                    text_override, model_used = await generate_cinematic_description(
                        title=movie.title or "",
                        overview=movie.overview or "",
                        genres=movie.genres or [],
                        keywords=keywords,
                        directors=movie.directors or [],
                        cast=movie.cast or [],
                        year=movie.year or 0,
                        groq_client=self.groq_client,
                    )
                    # Only mark as enriched if an LLM model actually produced it
                    if model_used is not None:
                        movie.has_enriched_embedding = True
                except Exception as e:
                    logger.warning(f"Cinematic enrichment failed for {movie.title}: {e}")
                    text_override = None

            loop = asyncio.get_running_loop()
            vector = await loop.run_in_executor(
                None,
                lambda: self.embedding_service.generate_embedding(
                    {"title": movie.title, "overview": movie.overview, "genres": movie.genres, "keywords": keywords},
                    text_override=text_override,
                )
            )

            # 6. Construct PointStruct (Qdrant)
            point = PointStruct(
                id=tmdb_id,
                vector=vector.tolist(),
                payload={
                    "tmdb_id": tmdb_id,
                    "title": movie.title,
                    "year": movie.year,
                    "genres": movie.genres,
                    "rating": movie.vote_average,
                    "vote_count": movie.vote_count,
                    "runtime": movie.runtime,
                    "poster_path": movie.poster_path,
                    "vectorbox_score": movie.vectorbox_score,
                    "imdb_rating": movie.imdb_rating,
                    "metacritic_rating": movie.metacritic_rating,
                    "title_es": movie.title_es,
                    "overview_es": movie.overview_es,
                    "keywords": movie.keywords,
                    "directors": movie.directors,
                    "cast": movie.cast
                }
            )

            # 7. Providers (Optional return or handled by caller)
            # The original movie_service logic handled providers separately after creation.
            # We can return the providers data if needed, but for now we'll stick to Movie/Point
            # and let the caller handle provider saving if they have the 'details' dict, 
            # BUT efficient batching means we might want to return details too?
            # For strict separation, `build_movie` returns the core entities.
            # The caller might need `details` for providers. 
            # Let's attach providers data to the Movie object transiently? No, that's messy.
            
            # IMPROVEMENT: Return `details` as a third element if needed, 
            # OR handle provider parsing here and return it.
            
            # Let's check usage. 
            # movie_service: saves providers if "providers_data" in details.
            # seed_db: skips providers or does them individually.
            
            # Best approach: Return a `MovieConstructionResult` dataclass?
            # For now, let's just attach the providers raw data to the movie object as a non-mapped attribute
            # strictly for transport, or return it.
            # Returning (Movie, Point, ProvidersData) seems cleanest.
            
            providers_data = details.get("providers_data", {}).get("ES", {})
            
            return movie, point, providers_data

        except Exception as e:
            logger.error(f"Error building movie {tmdb_id}: {e}")
            return None, None, None

    def _process_release_dates(self, details: Dict) -> Dict[str, str]:
        release_dates_map = {}
        if "release_dates" in details and "results" in details["release_dates"]:
            for country in details["release_dates"]["results"]:
                iso = country["iso_3166_1"]
                best_date = None
                for date_entry in country["release_dates"]:
                    if date_entry["type"] == 3: # Theatrical
                        best_date = date_entry["release_date"]
                        break 
                    elif date_entry["type"] == 4 and not best_date:
                        best_date = date_entry["release_date"]
                    elif not best_date:
                        best_date = date_entry["release_date"]
                
                if best_date:
                    release_dates_map[iso] = best_date.split("T")[0]
        return release_dates_map
