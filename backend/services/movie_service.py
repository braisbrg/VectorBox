import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from models.database import Movie
from services.tmdb_client import TMDBClient
from services.omdb_client import OMDbClient
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService
from services.provider_service import ProviderService
from services.movie_factory import MovieFactory

logger = logging.getLogger(__name__)

class MovieService:
    def __init__(self, db: AsyncSession, tmdb: TMDBClient = None):
        self.db = db
        self._owns_tmdb = tmdb is None
        self.tmdb = tmdb or TMDBClient()
        self.omdb = OMDbClient()
        self.qdrant = QdrantService()
        self.embedding_service = EmbeddingService()
        self.provider_service = ProviderService(db, self.tmdb)
        self.factory = MovieFactory(self.tmdb, self.omdb, self.embedding_service)

    async def get_or_create_movie(self, tmdb_id: int, letterboxd_uri: Optional[str] = None) -> Optional[Movie]:
        """
        Retrieves a movie from the DB or ingests it from TMDB/OMDb if missing.
        """
        # 1. Check DB
        stmt = select(Movie).where(Movie.tmdb_id == tmdb_id)
        result = await self.db.execute(stmt)
        movie = result.scalar_one_or_none()

        if movie:
            # Update URI if provided and missing
            if letterboxd_uri and not movie.letterboxd_uri:
                movie.letterboxd_uri = letterboxd_uri
                try:
                    await self.db.commit()
                except Exception as e:
                    await self.db.rollback()
                    logger.error(f"DB commit failed updating letterboxd_uri: {e}")
                    raise
            return movie

        # 2. Ingest if missing
        return await self.ingest_movie(tmdb_id, letterboxd_uri)

    async def ingest_movie(self, tmdb_id: int, letterboxd_uri: Optional[str] = None, skip_qdrant: bool = False) -> Optional[Movie]:
        """
        Fetches full metadata (TMDB + OMDb), saves to DB. 
        If skip_qdrant is False (default), upserts to Qdrant immediately.
        """
        try:
            logger.info(f"Ingesting movie TMDB ID: {tmdb_id}")
            
            # A. Build Movie & Point using Factory
            # This replaces the entire manual fetch -> build -> score -> vector pipeline
            movie, point, providers_raw = await self.factory.build_movie(tmdb_id, letterboxd_uri)
            
            if not movie:
                return None

            # B. Save to SQL
            self.db.add(movie)
            try:
                await self.db.commit()
                await self.db.refresh(movie)
            except Exception as e:
                await self.db.rollback()
                logger.error(f"DB commit failed ingesting new movie: {e}")
                raise
            logger.info(f"Created movie: {movie.title} (VB Score: {movie.vectorbox_score})")

            # C. Upsert to Qdrant (if not skipped)
            if not skip_qdrant and point:
                # Factory returns a generic point, we upsert it using service helper
                # Note: upsert_movie_vector helper expects explicit args, but we have a PointStruct.
                # Use batch upsert for single point to reuse the struct directly?
                # Or just map the fields? 
                # Converting PointStruct back to args for `upsert_movie_vector` is redundant.
                # Let's use `upsert_batch` for single point efficiency or `upsert_movie_vector` if we want to keep that method alive.
                # Actually, `QdrantService.upsert_batch` takes a list of `PointStruct`.
                await self.qdrant.upsert_batch([point])

            # D. Save Providers (if available)
            if providers_raw:
                # Flatten providers logic (same as original)
                providers_list = []
                for p_type in ["flatrate", "free"]:
                    if p_type in providers_raw:
                        providers_list.extend(providers_raw[p_type])
                
                if providers_list:
                     await self.provider_service.save_providers(movie.id, "ES", providers_list)

            return movie

        except Exception as e:
            await self.db.rollback()
            return None

    async def ensure_vector_exists(self, movie: Movie) -> bool:
        """
        idempotently ensures a vector exists for the movie in Qdrant.
        """
        try:
            # Check if vector exists
            exists = await self.qdrant.get_vector(movie.tmdb_id)
            if exists:
                return True
                
            logger.warning(f"Vector missing for {movie.title} ({movie.tmdb_id}). Regenerating...")
            
            # Generate Metadata
            keywords = await self.tmdb.get_movie_keywords(movie.tmdb_id)
            vector = self.embedding_service.generate_embedding({
                "title": movie.title,
                "overview": movie.overview,
                "genres": movie.genres,
                "keywords": keywords
            })

            await self.qdrant.upsert_movie_vector(
                movie_id=movie.tmdb_id,
                vector=vector.tolist(),
                metadata={
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
                    "rotten_tomatoes_rating": movie.rotten_tomatoes_rating,
                    "title_es": movie.title_es,
                    "overview_es": movie.overview_es
                }
            )
            return True
        except Exception as e:
            logger.error(f"Failed to repair vector for {movie.title}: {e}")
            return False

    async def enrich_movie(self, movie: Movie, skip_qdrant: bool = False) -> bool:
        """
        Ensures movie has keywords, OMDb data (VB Score), and a valid vector.
        Self-heals missing data.
        """
        try:
            changed = False
            details = None
            
            # 1. Check OMDb / VectorBox Score
            # Re-fetch if missing (None) or if it's 0 (invalid/empty initial fetch)
            if movie.vectorbox_score is None or movie.vectorbox_score == 0:
                logger.info(f"Enriching OMDb data for {movie.title} (Current Score: {movie.vectorbox_score})...")
                details = await self.tmdb.get_movie_details(movie.tmdb_id)
                if details and details.get("imdb_id"):
                    imdb_id = details.get("imdb_id")
                    omdb_data = await self.omdb.fetch_movie_data(imdb_id)
                    vb_score_obj = self.omdb.calculate_vectorbox_score(omdb_data, details.get("vote_average"))
                    
                    movie.imdb_id = imdb_id
                    movie.vectorbox_score = vb_score_obj.score
                    movie.imdb_rating = vb_score_obj.breakdown.imdb
                    movie.metacritic_rating = vb_score_obj.breakdown.meta
                    movie.rotten_tomatoes_rating = vb_score_obj.breakdown.rt
                    
                    changed = True
            
            # 2. Check/Fetch Keywords
            if not movie.keywords:
                logger.info(f"Enriching keywords for {movie.title} ({movie.tmdb_id})...")
                keywords = await self.tmdb.get_movie_keywords(movie.tmdb_id)
                if keywords:
                    movie.keywords = keywords
                    changed = True
            
            # 3. Check/Fetch Release Dates (New)
            if not movie.release_dates:
                 logger.info(f"Enriching release dates for {movie.title}...")
                 # We need full details again if we didn't fetch them above
                 # Optimization: reuse details if we fetched them for OMDb
                 if not details: 
                     details = await self.tmdb.get_movie_details(movie.tmdb_id)
                 
                 release_dates_map = {}
                 if details and "release_dates" in details and "results" in details["release_dates"]:
                     for country in details["release_dates"]["results"]:
                         iso = country["iso_3166_1"]
                         # Find theatrical release (type 3) or digital (4)
                         # Priority: 3 > 4 > anything
                         best_date = None
                         for date_entry in country["release_dates"]:
                             if date_entry["type"] == 3: # Theatrical
                                 best_date = date_entry["release_date"]
                                 break # Found best
                             elif date_entry["type"] == 4 and not best_date:
                                 best_date = date_entry["release_date"]
                             elif not best_date:
                                 best_date = date_entry["release_date"]
                         
                         if best_date:
                             release_dates_map[iso] = best_date.split("T")[0]
                     
                     if release_dates_map:
                         movie.release_dates = release_dates_map
                         changed = True
            
            if changed:
                try:
                    await self.db.commit()
                except Exception as e:
                    await self.db.rollback()
                    logger.error(f"DB commit failed enriching movie: {e}")
                    raise                # Force vector regeneration
                vector = self.embedding_service.generate_embedding({
                    "title": movie.title,
                    "overview": movie.overview,
                    "genres": movie.genres,
                    "keywords": movie.keywords or []
                })
                
                if not skip_qdrant:
                    from models.external_schemas import QdrantPayload
                    payload = QdrantPayload(
                        tmdb_id=movie.tmdb_id,
                        title=movie.title,
                        year=movie.year,
                        genres=movie.genres or [],
                        rating=movie.vote_average,
                        vote_count=movie.vote_count,
                        runtime=movie.runtime,
                        poster_path=movie.poster_path,
                        vectorbox_score=movie.vectorbox_score,
                        imdb_rating=movie.imdb_rating,
                        metacritic_rating=movie.metacritic_rating,
                        rotten_tomatoes_rating=movie.rotten_tomatoes_rating,
                        title_es=movie.title_es,
                        overview_es=movie.overview_es,
                        keywords=movie.keywords or []
                    )
                    
                    await self.qdrant.upsert_movie_vector(
                        movie_id=movie.tmdb_id,
                        vector=vector.tolist(),
                        metadata=payload
                    )
                return True
            
            # 3. Even if nothing changed, ensure vector exists (unless skipping)
            if not skip_qdrant:
                return await self.ensure_vector_exists(movie)
            return True
            
        except Exception as e:
            logger.error(f"Failed to enrich movie {movie.title}: {e}")
            return False

    async def close(self):
        if self._owns_tmdb:
            await self.tmdb.aclose()
        await self.omdb.aclose()
