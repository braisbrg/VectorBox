import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from models.database import Movie
from models.external_schemas import qdrant_payload
from services.tmdb_client import TMDBClient
from services.omdb_client import OMDbClient
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService
from services.provider_service import ProviderService
from services.movie_factory import MovieFactory

logger = logging.getLogger(__name__)
_enriching_now: set[int] = set()

class MovieService:
    def __init__(self, db: AsyncSession, tmdb: TMDBClient = None, groq_client=None):
        self.db = db
        self._owns_tmdb = tmdb is None
        self.tmdb = tmdb or TMDBClient()
        self._groq_client = groq_client
        self._qdrant = None
        self._embedding = None
        self._omdb = None
        self._provider_service = None
        self._factory = None

    @property
    def qdrant(self) -> QdrantService:
        if self._qdrant is None:
            self._qdrant = QdrantService()
        return self._qdrant

    @property
    def embedding(self) -> EmbeddingService:
        if self._embedding is None:
            self._embedding = EmbeddingService()
        return self._embedding

    @property
    def omdb(self) -> OMDbClient:
        if self._omdb is None:
            self._omdb = OMDbClient()
        return self._omdb

    @property
    def provider_service(self) -> ProviderService:
        if self._provider_service is None:
            self._provider_service = ProviderService(self.db, self.tmdb)
        return self._provider_service

    @property
    def factory(self) -> MovieFactory:
        if self._factory is None:
            self._factory = MovieFactory(
                self.tmdb, self.omdb, self.embedding, groq_client=self._groq_client
            )
        return self._factory

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
            movie, point, providers_raw = await self.factory.build_movie(tmdb_id, letterboxd_uri)

            if not movie:
                return None

            # A2. Refuse TMDB `adult` titles on every user-triggered ingest path
            # (rate, similar, group-vibe, rss, ZIP). Seed scripts call
            # MovieFactory.build_movie directly and are deliberately unaffected.
            # Without this, one authenticated user can inject arbitrary porn
            # into the catalogue every other user's feed reads from.
            if movie.is_adult:
                logger.warning(f"Rejected adult TMDB title at ingest: tmdb_id={tmdb_id}")
                return None

            # B. Upsert to Qdrant FIRST (REL-2). The point is keyed by tmdb_id
            # (movie_factory), so it needs nothing from the not-yet-committed PG
            # row. Doing the vector write before the DB commit means a Qdrant
            # failure aborts the whole ingest — we never persist a PG movie that
            # is invisible to vector search (BYW / Magic Box). The only residual
            # is a harmless orphan *vector* if the commit below then fails: a
            # point with no PG row is simply never returned to users, and a
            # later re-ingest of the same tmdb_id overwrites it in place.
            if not skip_qdrant and point:
                await self.qdrant.upsert_batch([point])

            # C. Save to SQL
            self.db.add(movie)
            try:
                await self.db.commit()
                await self.db.refresh(movie)
            except Exception as e:
                await self.db.rollback()
                logger.error(f"DB commit failed ingesting new movie: {e}")
                raise
            logger.info(f"Created movie: {movie.title} (VB Score: {movie.vectorbox_score})")

            # D. Save Providers (if available)
            if providers_raw:
                providers_list = []
                for p_type in ["flatrate", "free"]:
                    if p_type in providers_raw:
                        providers_list.extend(providers_raw[p_type])
                
                if providers_list:
                     await self.provider_service.save_providers(movie.id, "ES", providers_list)

            return movie

        except Exception as e:
            # OBS-2: this used to swallow `e` silently (return None), hiding the
            # cause of every failed ingest — including, now, a Qdrant upsert that
            # aborts the ingest under the REL-2 ordering. Log before bailing.
            await self.db.rollback()
            logger.error(f"Failed to ingest movie tmdb_id={tmdb_id}: {e}", exc_info=True)
            return None

    async def ensure_vector_exists(self, movie: Movie) -> bool:
        """
        idempotently ensures a vector exists for the movie in Qdrant.
        """
        try:
            exists = await self.qdrant.get_vector(movie.tmdb_id)
            if exists:
                return True
                
            logger.warning(f"Vector missing for {movie.title} ({movie.tmdb_id}). Regenerating...")

            keywords = await self.tmdb.get_movie_keywords(movie.tmdb_id)
            # AGENTS.md: when re-encoding is unavoidable, prefer the Groq
            # cinematic_description (the catalogue encoding) over the
            # overview+genres+keywords fallback — otherwise the regenerated
            # vector lives in a different text space than its neighbours.
            text_override = movie.cinematic_description or None
            loop = asyncio.get_running_loop()
            vector = await loop.run_in_executor(
                None,
                lambda: self.embedding.generate_embedding({
                    "title": movie.title,
                    "overview": movie.overview,
                    "genres": movie.genres,
                    "keywords": keywords
                }, text_override=text_override)
            )

            await self.qdrant.upsert_movie_vector(
                movie_id=movie.tmdb_id,
                vector=vector.tolist(),
                metadata=qdrant_payload(movie)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to repair vector for {movie.title}: {e}")
            return False

    async def enrich_movie(self, movie: Movie, skip_qdrant: bool = False, force: bool = False) -> bool:
        """
        Ensures movie has keywords, OMDb data (VB Score), and a valid vector.
        Self-heals missing data.
        """
        if movie.tmdb_id in _enriching_now:
            logger.debug(f"Skipping enrich for {movie.title} — already in progress")
            return False

        _enriching_now.add(movie.tmdb_id)

        try:
            changed = False
            details = None

            # 1. Check OMDb / VectorBox Score (FIX: Solo reintenta si es None o falta IMDb)
            if movie.vectorbox_score is None or movie.imdb_id is None or movie.imdb_rating is None or force:
                logger.info(f"Enriching OMDb data for {movie.title}...")
                details = await self.tmdb.get_movie_details(movie.tmdb_id)
                # Normalize empty string / missing → None (TMDB sometimes returns "")
                imdb_id = ((details or {}).get("imdb_id") or "").strip() or None
                if imdb_id:
                    omdb_data = await self.omdb.fetch_movie_data(imdb_id)
                    vb_score_obj = self.omdb.calculate_vectorbox_score(
                        omdb_data,
                        details.get("vote_average"),
                        tmdb_vote_count=details.get("vote_count"),
                    )

                    movie.imdb_id = imdb_id
                    movie.vectorbox_score = vb_score_obj.score
                    movie.imdb_rating = vb_score_obj.breakdown.imdb
                    movie.metacritic_rating = vb_score_obj.breakdown.meta
                    changed = True

            # 2. Check/Fetch Keywords (FIX: Si no tiene, guarda un array vacío para no volver a buscar)
            if movie.keywords is None:
                logger.info(f"Enriching keywords for {movie.title} ({movie.tmdb_id})...")
                keywords = await self.tmdb.get_movie_keywords(movie.tmdb_id)
                movie.keywords = keywords if keywords else[]
                changed = True

            # 3. Check/Fetch Release Dates (FIX: Guarda un diccionario vacío si no hay datos)
            if movie.release_dates is None:
                logger.info(f"Enriching release dates for {movie.title}...")
                if not details:
                    details = await self.tmdb.get_movie_details(movie.tmdb_id)

                release_dates_map = {}
                if details and "release_dates" in details and "results" in details["release_dates"]:
                    for country in details["release_dates"]["results"]:
                        iso = country["iso_3166_1"]
                        best_date = None
                        for date_entry in country["release_dates"]:
                            if date_entry["type"] == 3:  # Theatrical
                                best_date = date_entry["release_date"]
                                break
                            elif date_entry["type"] == 4 and not best_date:
                                best_date = date_entry["release_date"]
                            elif not best_date:
                                best_date = date_entry["release_date"]

                        if best_date:
                            release_dates_map[iso] = best_date.split("T")[0]

                movie.release_dates = release_dates_map if release_dates_map else {}
                changed = True

            if changed:
                try:
                    await self.db.commit()
                except Exception as e:
                    await self.db.rollback()
                    logger.error(f"DB commit failed enriching movie: {e}")
                    raise

                # Same rule as ensure_vector_exists: re-encode from the
                # cinematic_description when the movie has one, so a metadata
                # refresh (OMDb/keywords/release_dates) can never silently
                # replace a Groq-enriched vector with the fallback recipe.
                text_override = movie.cinematic_description or None
                loop = asyncio.get_running_loop()
                vector = await loop.run_in_executor(
                    None,
                    lambda: self.embedding.generate_embedding({
                        "title": movie.title,
                        "overview": movie.overview,
                        "genres": movie.genres,
                        "keywords": movie.keywords or []
                    }, text_override=text_override)
                )

                if not skip_qdrant:
                    payload = qdrant_payload(movie)

                    await self.qdrant.upsert_movie_vector(
                        movie_id=movie.tmdb_id,
                        vector=vector.tolist(),
                        metadata=payload
                    )
                return True

            if not skip_qdrant:
                return await self.ensure_vector_exists(movie)
            return True

        except Exception as e:
            logger.error(f"Failed to enrich movie {movie.title}: {e}")
            return False

        finally:
            _enriching_now.discard(movie.tmdb_id)

    async def close(self):
        if self._owns_tmdb:
            await self.tmdb.aclose()
        if self._omdb is not None:
            await self._omdb.close()
        # _qdrant is always lazily self-created (never injected), so closing
        # it here can't kill a shared singleton.
        if self._qdrant is not None:
            await self._qdrant.aclose()