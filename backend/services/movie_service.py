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

logger = logging.getLogger(__name__)

class MovieService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.tmdb = TMDBClient()
        self.omdb = OMDbClient()
        self.qdrant = QdrantService()
        self.embedding_service = EmbeddingService()
        self.provider_service = ProviderService(db, self.tmdb)

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
                await self.db.commit()
            return movie

        # 2. Ingest if missing
        return await self.ingest_movie(tmdb_id, letterboxd_uri)

    async def ingest_movie(self, tmdb_id: int, letterboxd_uri: Optional[str] = None) -> Optional[Movie]:
        """
        Fetches full metadata (TMDB + OMDb), saves to DB, and upserts to Qdrant.
        """
        try:
            logger.info(f"Ingesting movie TMDB ID: {tmdb_id}")
            
            # A. Fetch TMDB Details
            details = await self.tmdb.get_movie_details(tmdb_id)
            if not details:
                logger.warning(f"TMDB ID {tmdb_id} not found.")
                return None

            # B. Fetch OMDb Data (VectorBox Score)
            imdb_id = details.get("imdb_id")
            omdb_data = {}
            if imdb_id:
                omdb_data = await self.omdb.fetch_movie_data(imdb_id) or {}
            
            vb_score_data = self.omdb.calculate_vectorbox_score(omdb_data, details.get("vote_average"))

            # C. Create Movie Record
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
                vote_average=details.get("vote_average"),
                vote_count=details.get("vote_count"),
                popularity=details.get("popularity"),
                original_language=details.get("original_language"),
                letterboxd_uri=letterboxd_uri,
                
                # Phase 12 Fields
                imdb_id=imdb_id,
                imdb_rating=vb_score_data["breakdown"]["imdb"],
                metacritic_rating=vb_score_data["breakdown"]["meta"],
                rotten_tomatoes_rating=vb_score_data["breakdown"]["rt"],
                vectorbox_score=vb_score_data["score"],
                title_es=details.get("title_es"),
                overview_es=details.get("overview_es"),
                collection_id=details.get("belongs_to_collection", {}).get("id") if details.get("belongs_to_collection") else None,
                keywords=details.get("keywords_flat", [])
            )

            self.db.add(movie)
            await self.db.commit()
            await self.db.refresh(movie)
            logger.info(f"Created movie: {movie.title} (VB Score: {movie.vectorbox_score})")

            # D. Upsert to Qdrant
            # Use the keywords we just fetched
            keywords = movie.keywords or []
            
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

            # E. Cache Providers
            await self.provider_service.get_providers(movie.id, "ES")

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

    async def enrich_movie(self, movie: Movie) -> bool:
        """
        Ensures movie has keywords and a valid vector.
        Self-heals missing data.
        """
        try:
            # 1. Check/Fetch Keywords
            if not movie.keywords:
                logger.info(f"Enriching keywords for {movie.title} ({movie.tmdb_id})...")
                keywords = await self.tmdb.get_movie_keywords(movie.tmdb_id)
                if keywords:
                    movie.keywords = keywords
                    await self.db.commit()
                    
                    # Force vector regeneration since keywords changed
                    vector = self.embedding_service.generate_embedding({
                        "title": movie.title,
                        "overview": movie.overview,
                        "genres": movie.genres,
                        "keywords": movie.keywords
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
                            "overview_es": movie.overview_es,
                            "keywords": movie.keywords
                        }
                    )
                    return True
            
            # 2. Even if keywords existed, ensure vector exists
            return await self.ensure_vector_exists(movie)
            
        except Exception as e:
            logger.error(f"Failed to enrich movie {movie.title}: {e}")
            return False

    async def close(self):
        await self.tmdb.close()
