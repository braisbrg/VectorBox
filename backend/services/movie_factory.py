import asyncio
import logging
import re
from typing import Optional, Tuple, Dict, Any, List
from datetime import datetime

from models.database import Movie
from services.tmdb_client import TMDBClient
from services.omdb_client import OMDbClient, parse_oscar_wins, split_omdb_csv
from services.embedding_service import EmbeddingService
from services.cinematic_enricher import generate_cinematic_description
from qdrant_client.models import PointStruct

logger = logging.getLogger(__name__)


# Narrow title regex — each alternative must NOT match real film titles. Avoid
# generic words like "Special" / "Tour" / "Concert" alone (too many FPs).
# Calibrated against the catalog probe (scripts/probe_non_film_heuristic.py).
_NON_FILM_TITLE_RE = re.compile(
    r"(?i)\b(?:"
    r"UFC\s\d+"
    r"|WWE\s"
    r"|AEW\s(?:Double|Revolution|Dynamite|All\sOut|Full\sGear|Forbidden|Collision)"
    r"|ROH\s(?:Supercard|Final\sBattle|Death\sBefore\sDishonor)"
    r"|ONE\s(?:Championship|Fight\sNight)"
    r"|Bellator\s\d+"
    r"|Glory\s\d+"
    r"|PRIDE\s(?:FC|\d+)"
    r"|WrestleMania\s\d"
    r"|SummerSlam\s\d"
    r"|Royal\sRumble\s\d"
    r"|NXT\sTakeOver"
    r"|IMPACT\sWrestling"
    r"|TNA\s(?:Slammiversary|Bound\sfor\sGlory)"
    r"|Looney\sTunes\sCollector"
    r")\b"
)


def is_likely_non_film(
    title: Optional[str],
    year: Optional[int],
    runtime: Optional[int],
    genres: Optional[List[str]],
    directors: Optional[List[str]],
    overview: Optional[str],
) -> bool:
    """Heuristic flag for "not really a film": UFC/AEW/wrestling events,
    multi-hour cartoon recopilations, fight nights, etc.

    The rule is calibrated to be **conservative** (high precision, accepting
    we miss some). False positives would silently hide real indie films, which
    is worse than letting one UFC event slip through.

    Two independent gates — flag if EITHER triggers:

      (a) Strict title whitelist (`_NON_FILM_TITLE_RE`) — for events whose
          names are unambiguous regardless of metadata.

      (b) Structural heuristic: `directors == []` is required (real films
          almost always have ≥1 director in TMDB; events practically never)
          AND at least 2 other "non-film" signals — thin overview, anomalous
          runtime, or only-Action/empty genres.

    Future films are exempt from (b) — they're TMDB-poor by construction
    (announced but no metadata yet); Phase 1 fills them in as release date
    approaches. We don't want to flag "Narnia 2027" just because TMDB
    hasn't catalogued it yet.
    """
    # Gate (a): explicit title patterns
    if title and _NON_FILM_TITLE_RE.search(title):
        return True

    # Future films: TMDB-poor by construction, skip heuristic
    current_year = datetime.utcnow().year
    if year is not None and year > current_year:
        return False

    # Gate (b): structural heuristic — director absent is the anchor signal
    if directors and len(directors) > 0:
        return False  # real films have directors, events don't

    other_signals = 0
    if not overview or len(overview.strip()) < 50:
        other_signals += 1
    if runtime is None or runtime == 0 or runtime > 240:
        other_signals += 1
    if not genres or genres == ["Action"]:
        other_signals += 1

    return other_signals >= 2

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

    async def build_movie(self, tmdb_id: int, letterboxd_uri: Optional[str] = None) -> Tuple[Optional[Movie], Optional[PointStruct], Optional[Dict]]:
        """
        Orchestrates the full pipeline:
        1. Fetch TMDB Details
        2. Fetch/Calculate OMDb Data (VectorBox Score)
        3. Parse Release Dates
        4. Construct SQL Model
        5. Generate Embedding
        6. Construct Qdrant Point

        Returns: (Movie, PointStruct, providers_data) or (None, None, None) if failed.
        """
        try:
            # 1. Fetch TMDB Details
            details = await self.tmdb.get_movie_details(tmdb_id)
            if not details:
                logger.warning(f"TMDB ID {tmdb_id} not found.")
                return None, None, None

            # 2. Fetch OMDb Data (VectorBox Score)
            # Normalize empty string → None. TMDB returns imdb_id="" for shorts
            # and unreleased films; without this, the UNIQUE constraint on
            # Movie.imdb_id treats multiple "" as duplicates and the second
            # insert blows up. NULLs are fine — Postgres treats them as distinct.
            imdb_id = (details.get("imdb_id") or "").strip() or None
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

            # Pre-compute the non-film flag from the materialised metadata
            # (matches the columns we're about to write to the SQL row).
            _year = int(details.get("release_date", "0000")[:4]) if details.get("release_date") else None
            _genres = [g["name"] for g in details.get("genres", [])]
            _directors = details.get("directors", [])
            _excluded = is_likely_non_film(
                title=details.get("title"),
                year=_year,
                runtime=details.get("runtime"),
                genres=_genres,
                directors=_directors,
                overview=details.get("overview"),
            )
            if _excluded:
                logger.info(
                    f"[ingest] tmdb_id={tmdb_id} flagged is_excluded=True ('{details.get('title')}') "
                    f"— will be hidden from recommendations, visible in user-chosen surfaces"
                )

            # 4. Construct Movie Object (SQL)
            movie = Movie(
                tmdb_id=tmdb_id,
                title=details.get("title"),
                original_title=details.get("original_title"),
                year=_year,
                runtime=details.get("runtime"),
                genres=_genres,
                overview=details.get("overview"),
                poster_path=details.get("poster_path"),
                backdrop_path=details.get("backdrop_path"),
                tagline=details.get("tagline") or None,
                is_adult=bool(details.get("adult", False)),
                is_excluded=_excluded,
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
                        movie.enriched_by_model = model_used
                        movie.cinematic_description = text_override
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
