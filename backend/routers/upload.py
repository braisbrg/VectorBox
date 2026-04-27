"""
Upload router for CSV file processing
Security: File validation, rate limiting, background tasks
v1.1: Uses Redis-based TaskStore for progress tracking
"""
import asyncio
from fastapi import APIRouter, UploadFile, File, Depends, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from slowapi import Limiter
from slowapi.util import get_remote_address
import logging

from config import get_db
from dependencies import get_current_user, verify_user_ownership, get_embedding_service, get_qdrant_service
from services.embedding_service import EmbeddingService
from services.qdrant_service import QdrantService
from services.clustering_service import ClusteringService
from services.provider_service import ProviderService

from services.data_processor import DataProcessor
from services.task_store import get_task_store
from models.database import User, Movie, UserRating
from models.schemas import CSVUploadResponse, TokenResponse

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

# Legacy in-memory status (kept for backwards compatibility)
upload_status = {}

async def _enrich_user_movies_background(user_id: int) -> None:
    """
    Post-upload enrichment: enrich movies imported by this user that lack Scout embeddings.
    Runs silently after upload completes. On finish, invalidates cache and re-clusters.
    """
    import os
    from openai import AsyncOpenAI
    from config import AsyncSessionLocal
    from services.cinematic_enricher import generate_cinematic_description
    from services.cache_service import invalidate_user_cache

    async with AsyncSessionLocal() as db:
        try:
            stmt = (
                select(Movie)
                .join(UserRating, Movie.id == UserRating.movie_id)
                .where(UserRating.user_id == user_id)
                .where(Movie.has_enriched_embedding.is_(False))
                .where(Movie.vectorbox_score.isnot(None))
                .distinct()
            )
            result = await db.execute(stmt)
            movies_to_enrich = result.scalars().all()

            if not movies_to_enrich:
                logger.info(f"[Enrichment] No movies to enrich for user {user_id}")
                return

            logger.info(f"[Enrichment] Starting enrichment of {len(movies_to_enrich)} movies for user {user_id}")

            groq_key = os.getenv("GROQ_API_KEY")
            gemini_key = os.getenv("GEMINI_API_KEY")
            if groq_key:
                llm_client = AsyncOpenAI(
                    api_key=groq_key,
                    base_url="https://api.groq.com/openai/v1",
                    max_retries=0,
                )
            elif gemini_key:
                llm_client = AsyncOpenAI(
                    api_key=gemini_key,
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                )
            else:
                logger.warning("[Enrichment] No LLM API key available, skipping enrichment")
                return

            qdrant = QdrantService()
            embedding_service = await get_embedding_service()
            enriched_count = 0
            batch_size = 10

            try:
                for i in range(0, len(movies_to_enrich), batch_size):
                    batch = movies_to_enrich[i:i + batch_size]

                    for movie in batch:
                        try:
                            description, model_used = await generate_cinematic_description(
                                title=movie.title or "",
                                overview=movie.overview or "",
                                genres=movie.genres or [],
                                keywords=movie.keywords or [],
                                directors=movie.directors or [],
                                cast=movie.cast or [],
                                year=movie.year or 0,
                                groq_client=llm_client,
                            )
                            if not description or model_used is None:
                                continue

                            loop = asyncio.get_event_loop()
                            vector = await loop.run_in_executor(
                                None,
                                lambda d=description, m=movie: embedding_service.generate_embedding(
                                    {"title": m.title, "overview": m.overview, "genres": m.genres, "keywords": m.keywords or []},
                                    text_override=d,
                                )
                            )
                            if vector is None:
                                continue

                            payload = {
                                "tmdb_id": movie.tmdb_id,
                                "title": movie.title,
                                "year": movie.year,
                                "genres": movie.genres or [],
                                "overview": movie.overview or "",
                                "poster_path": movie.poster_path,
                                "vote_average": movie.vote_average,
                                "vote_count": movie.vote_count,
                                "runtime": movie.runtime,
                                "original_language": movie.original_language,
                                "keywords": movie.keywords or [],
                                "directors": movie.directors,
                                "cast": movie.cast,
                                "vectorbox_score": movie.vectorbox_score,
                                "imdb_rating": movie.imdb_rating,
                                "metacritic_rating": movie.metacritic_rating,
                                "title_es": movie.title_es,
                                "overview_es": movie.overview_es,
                            }
                            await qdrant.upsert_movie_vector(
                                movie_id=movie.tmdb_id,
                                vector=vector.tolist(),
                                metadata=payload,
                            )

                            movie.has_enriched_embedding = True
                            movie.enriched_by_model = model_used
                            db.add(movie)
                            enriched_count += 1

                        except Exception as e:
                            logger.warning(f"[Enrichment] Failed for movie {movie.id}: {e}")
                            continue

                    await db.commit()

                    if i + batch_size < len(movies_to_enrich):
                        await asyncio.sleep(1.0)

                logger.info(f"[Enrichment] Completed: {enriched_count}/{len(movies_to_enrich)} enriched for user {user_id}")

            finally:
                await llm_client.close()

            # T-03: Sanity-check embeddings against a MiniLM reference vector built from
            # title/year/genres/directors. Films flagged below 0.25 are excluded from anchor
            # and medoid selection downstream.
            logger.info(f"[Sanity] Running embedding quality check for user {user_id}")
            import numpy as np
            flagged = 0
            for movie in movies_to_enrich:
                stored = await qdrant.get_vector(movie.tmdb_id)
                if not stored:
                    continue
                ref_text = (
                    f"{movie.title or ''} {movie.year or ''} "
                    f"{' '.join(movie.genres or [])} {' '.join(movie.directors or [])}"
                ).strip()
                if not ref_text:
                    continue
                loop = asyncio.get_event_loop()
                try:
                    ref_vec = await loop.run_in_executor(
                        None,
                        lambda t=ref_text, m=movie: embedding_service.generate_embedding(
                            {"title": m.title or ""}, text_override=t
                        ),
                    )
                except Exception as e:
                    logger.warning(f"[Sanity] reference embedding failed for {movie.title}: {e}")
                    continue
                if ref_vec is None:
                    continue
                a = np.array(stored)
                b = np.array(ref_vec)
                denom = float(np.linalg.norm(a) * np.linalg.norm(b))
                if denom == 0:
                    continue
                quality = float(np.dot(a, b) / denom)
                movie.embedding_quality_score = quality
                if quality < 0.25:
                    flagged += 1
                    movie.has_enriched_embedding = False
                    logger.warning(f"[Sanity] Low quality embedding: {movie.title} ({quality:.2f})")
            await db.commit()
            logger.info(f"[Sanity] Check complete: {flagged} movies flagged for re-enrichment")

            logger.info(f"[Enrichment] Re-clustering user {user_id} with enriched embeddings")
            clustering = ClusteringService(qdrant=qdrant)
            await clustering.create_user_clusters(user_id, db)

            await invalidate_user_cache(user_id)
            logger.info(f"[Enrichment] Pipeline complete for user {user_id}")

        except Exception as e:
            logger.error(f"[Enrichment] Pipeline failed for user {user_id}: {e}")


@router.get("/status/{user_id}")
async def get_upload_status(
    user_id: int,
    current_user: TokenResponse = Depends(verify_user_ownership)
):
    """Get current upload status for user (legacy endpoint)"""
    return upload_status.get(user_id, {"status": "idle", "message": "", "progress": 0})

async def process_single_movie(
    movie_data: dict,
    user_id: int,
    tmdb_client: "TMDBClient",
    groq_client=None
):
    """
    Helper to process a single movie in a batch.
    Each call owns its own AsyncSession (safe for asyncio.gather).
    Returns: (movie_id, needs_vector_update) or (None, False) on failure.

    Matching priority:
      1. Local DB lookup by letterboxd_uri (most reliable, no HTTP)
      2. TMDB search by title+year with strict year validation (±1 tolerance)
    """
    from config import AsyncSessionLocal
    from services.movie_service import MovieService

    title = movie_data.get("title")
    year = movie_data.get("year")
    letterboxd_uri = movie_data.get("letterboxd_uri")

    try:
        # Fresh session per concurrent task (Architect Rule §2)
        async with AsyncSessionLocal() as session:
            movie_service = MovieService(session, tmdb=tmdb_client, groq_client=groq_client)
            try:
                # --- Step 1: Local DB lookup by letterboxd_uri ---
                if letterboxd_uri:
                    result = await session.execute(
                        select(Movie).where(Movie.letterboxd_uri == letterboxd_uri)
                    )
                    existing_movie = result.scalar_one_or_none()

                    if existing_movie:
                        logger.info(f"Found movie by letterboxd_uri: {existing_movie.title}")
                        updated = await movie_service.enrich_movie(existing_movie, skip_qdrant=True)
                        await session.commit()
                        return existing_movie.id, updated

                # --- Step 2: TMDB search with strict year validation ---
                tmdb_result = await tmdb_client.search_movie(title, year)

                if not tmdb_result:
                    logger.debug(f"No TMDB result for '{title}' ({year}) — may be a TV show or regional title")
                    return None, False

                # FIX 1: Reject TV show results — movies have release_date, TV shows have first_air_date.
                # search_movie calls /search/movie so this is defensive; guards against future /search/multi use.
                if tmdb_result.get("first_air_date") and not tmdb_result.get("release_date"):
                    logger.debug(f"Skipping TV show result for '{title}' ({year})")
                    return None, False

                # Strict year validation: reject if difference > 1
                if year:
                    result_date = tmdb_result.get("release_date", "")
                    result_year = int(result_date[:4]) if result_date and len(result_date) >= 4 else None
                    if result_year:
                        year_diff = abs(result_year - year)
                        if year_diff > 1:
                            logger.warning(
                                f"Rejected TMDB match for '{title}' ({year}): "
                                f"got {tmdb_result.get('title')} ({result_year})"
                            )
                            return None, False
                        elif year_diff == 1:
                            logger.warning(
                                f"Accepted TMDB match for '{title}' with ±1 year tolerance: "
                                f"expected {year}, got {result_year}"
                            )

                tmdb_id = tmdb_result["id"]

                # Check if movie already exists in DB by tmdb_id
                result = await session.execute(select(Movie).where(Movie.tmdb_id == tmdb_id))
                existing_movie = result.scalar_one_or_none()

                if existing_movie:
                    updated = await movie_service.enrich_movie(existing_movie, skip_qdrant=True)
                    await session.commit()
                    return existing_movie.id, updated
                else:
                    new_movie = await movie_service.ingest_movie(
                        tmdb_id=tmdb_id,
                        letterboxd_uri=letterboxd_uri,
                        skip_qdrant=True
                    )
                    await session.commit()
                    return new_movie.id if new_movie else None, True
            finally:
                await movie_service.close()

    except Exception as e:
        logger.error(f"Error processing movie {title}: {e}")
        return None, False


async def enrich_movies_background(
    movies_data: list,
    user_id: int,
    task_id: str = None
):
    """
    Background task to enrich movies with TMDB data and generate embeddings.
    Refactored v2.1: Owns its own AsyncSession (fixes MissingGreenlet).
    Chunked processing (Batch 50) + Parallel Fetch + Batch Upsert
    """
    from config import AsyncSessionLocal
    from services.movie_service import MovieService

    embedding_service = await get_embedding_service()
    qdrant = await get_qdrant_service()
    task_store = get_task_store()

    total_movies = len(movies_data)
    CHUNK_SIZE = 50
    enriched_count = 0

    # Legacy status init
    upload_status[user_id] = {
        "status": "processing",
        "message": "Starting batch enrichment...",
        "progress": 0,
        "total": total_movies,
        "current": 0
    }

    if task_id:
        await task_store.update_progress(task_id, 0, f"Starting batch processing of {total_movies} movies...")

    import asyncio

    try:
        # Shared TMDB client for parallel HTTP lookups (HTTP-safe, no DB)
        from services.tmdb_client import TMDBClient
        tmdb_client = TMDBClient()

        import os
        from openai import AsyncOpenAI
        if os.getenv("GROQ_API_KEY"):
            groq_client = AsyncOpenAI(
                api_key=os.getenv("GROQ_API_KEY"),
                base_url="https://api.groq.com/openai/v1",
                max_retries=0,
            )
        elif os.getenv("GEMINI_API_KEY"):
            groq_client = AsyncOpenAI(
                api_key=os.getenv("GEMINI_API_KEY"),
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
        else:
            groq_client = None

        try:
            async with AsyncSessionLocal() as db:
                # Clear existing ratings for clean re-import
                from sqlalchemy import delete as sa_delete
                await db.execute(sa_delete(UserRating).where(UserRating.user_id == user_id))
                await db.commit()

                # Process in Chunks
                for i in range(0, total_movies, CHUNK_SIZE):
                    chunk = movies_data[i : i + CHUNK_SIZE]
                    chunk_idx = (i // CHUNK_SIZE) + 1
                    total_chunks = (total_movies + CHUNK_SIZE - 1) // CHUNK_SIZE

                    logger.info(f"Processing Batch {chunk_idx}/{total_chunks} ({len(chunk)} movies)...")

                    # Update Progress
                    progress = int((i / total_movies) * 80)
                    msg = f"Processing Batch {chunk_idx}/{total_chunks}"

                    if task_id:
                        await task_store.update_progress(task_id, progress, msg)

                    # 1. Parallel Resolve & Ingest (each task owns its own session)
                    # FIX 5: Skip Scout enrichment during bulk upload to preserve quota.
                    # The nightly enrich_vectors.py --enrich-embeddings script handles enrichment.
                    tasks = []
                    for m_data in chunk:
                        tasks.append(process_single_movie(m_data, user_id, tmdb_client, groq_client=None))

                    # Results: list of (movie_id | None, needs_vector)
                    results = await asyncio.gather(*tasks)

                    # 2. Update User Ratings — SERIAL on main session (safe)
                    movies_to_vectorize_ids = []

                    for movie_data, (movie_id, needs_vector) in zip(chunk, results):
                        if not movie_id:
                            continue

                        try:
                            # 3. UPSERT Rating Safely
                            stmt = insert(UserRating).values(
                                user_id=user_id,
                                movie_id=movie_id,
                                rating=movie_data.get("rating"),
                                is_watchlist=movie_data.get("is_watchlist", False),
                                is_liked=movie_data.get("is_liked", False),
                                is_watched=movie_data.get("is_watched", False),
                                watched_date=movie_data.get("watched_date"),
                                review=movie_data.get("review"),
                                watch_count=movie_data.get("watch_count", 1),
                            ).on_conflict_do_update(
                                index_elements=["user_id", "movie_id"],
                                set_={
                                    "rating": getattr(insert(UserRating).excluded, "rating"),
                                    "is_watchlist": getattr(insert(UserRating).excluded, "is_watchlist"),
                                    "is_liked": getattr(insert(UserRating).excluded, "is_liked"),
                                    "is_watched": getattr(insert(UserRating).excluded, "is_watched"),
                                    "watched_date": getattr(insert(UserRating).excluded, "watched_date"),
                                    "review": getattr(insert(UserRating).excluded, "review"),
                                    "watch_count": getattr(insert(UserRating).excluded, "watch_count"),
                                }
                            )
                            await db.execute(stmt)

                            if needs_vector:
                                movies_to_vectorize_ids.append(movie_id)

                        except Exception as e:
                            logger.error(f"Rating upsert failed for movie_id {movie_id}: {e}")

                    # Commit batch DB changes
                    try:
                        await db.commit()
                    except Exception as e:
                        await db.rollback()
                        logger.error(f"DB commit failed during movie batch update: {e}")
                        raise

                    # 3. Batch Vectorize & Upsert (Qdrant Phase)
                    if movies_to_vectorize_ids:
                        try:
                            # Fetch movie objects by IDs from main session
                            result = await db.execute(
                                select(Movie).where(Movie.id.in_(movies_to_vectorize_ids))
                            )
                            movies_to_vectorize = result.scalars().all()

                            logger.info(f"Generating vectors for {len(movies_to_vectorize)} movies in batch...")

                            # Prepare data dicts for embedding service
                            data_for_embedding = []
                            for m in movies_to_vectorize:
                                data_for_embedding.append({
                                    "title": m.title,
                                    "overview": m.overview,
                                    "genres": m.genres,
                                    "keywords": m.keywords or []
                                })

                            # Generate Batch Embeddings (non-blocking)
                            loop = asyncio.get_event_loop()
                            vectors = await loop.run_in_executor(
                                None,
                                lambda: embedding_service.generate_batch_embeddings(data_for_embedding)
                            )

                            # Prepare Qdrant Points
                            from qdrant_client.models import PointStruct
                            points = []
                            for idx, m in enumerate(movies_to_vectorize):
                                points.append(PointStruct(
                                    id=m.tmdb_id,
                                    vector=vectors[idx].tolist(),
                                    payload={
                                        "title": m.title,
                                        "year": m.year,
                                        "genres": m.genres,
                                        "rating": m.vote_average,
                                        "vote_count": m.vote_count,
                                        "runtime": m.runtime,
                                        "poster_path": m.poster_path,
                                        "vectorbox_score": m.vectorbox_score,
                                        "imdb_rating": m.imdb_rating,
                                        "metacritic_rating": m.metacritic_rating,
                                        "rotten_tomatoes_rating": m.rotten_tomatoes_rating,
                                        "title_es": m.title_es,
                                        "overview_es": m.overview_es,
                                        "keywords": m.keywords
                                    }
                                ))

                            # Upsert Batch
                            if points:
                                await qdrant.upsert_batch(points)
                                enriched_count += len(points)

                        except Exception as e:
                            logger.error(f"Batch vector upsert failed: {e}")

                    # End of Chunk Loop

                # After all movies processed, create clusters
                if task_id:
                    await task_store.update_progress(task_id, 85, "Analyzing your taste profile...")

                try:
                    clustering = ClusteringService(qdrant=qdrant)
                    await clustering.create_user_clusters(user_id, db, groq_client=groq_client)
                    logger.info(f"Created clusters for user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to create clusters: {e}")

                # v1.1: Pre-cache feed data (Eager Calculation)
                if task_id:
                    await task_store.update_progress(task_id, 95, "Pre-caching your feed...")

                try:
                    # Invalidate any existing cache for this user
                    from services.cache_service import invalidate_user_cache
                    await invalidate_user_cache(user_id)
                    logger.info(f"Invalidated cache for user {user_id}")
                except Exception as e:
                    logger.warning(f"Cache invalidation failed: {e}")

        finally:
            # Close the shared TMDB HTTP client
            await tmdb_client.aclose()
            # FIX 5: Close the shared Groq client
            if groq_client:
                await groq_client.close()

        # Mark as complete (outside the session context — uses in-memory dict + Redis)
        upload_status[user_id] = {
            "status": "completed",
            "message": "Upload complete!",
            "progress": 100
        }

        if task_id:
            await task_store.complete_task(task_id, "Upload complete!")

        asyncio.create_task(_enrich_user_movies_background(user_id))
        logger.info(f"[Upload] Scheduled post-upload enrichment for user {user_id}")

    except Exception as e:
        logger.error(f"Background enrichment failed for user {user_id}: {e}")
        upload_status[user_id] = {
            "status": "error",
            "message": f"Enrichment failed: {str(e)}",
            "progress": 0
        }
        if task_id:
            try:
                await task_store.update_progress(task_id, -1, f"Error: {str(e)}")
            except Exception:
                pass



@router.post("/export", response_model=CSVUploadResponse)
@limiter.limit("5/minute")  # Security: Rate limit uploads
async def upload_export(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload Letterboxd export ZIP file.
    Parses ratings, watchlist, likes, and watched history.
    """
    user_id = current_user.user_id

    try:
        # Parse ZIP
        # Security: Validate file size
        MAX_FILE_SIZE = 10 * 1024 * 1024 # 10MB
        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)
        
        if size > MAX_FILE_SIZE:
             raise HTTPException(status_code=413, detail="File too large (Max 10MB)")

        # Security: Zip Bomb & Path Traversal Check
        import zipfile
        import io
        
        content = await file.read()
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                # Check for zip bomb (compression ratio)
                total_size = sum(info.file_size for info in zf.infolist())
                if total_size > 100 * 1024 * 1024: # Max 100MB extracted
                    raise HTTPException(status_code=400, detail="Decompression bomb detected")
                
                # Check for path traversal
                for info in zf.infolist():
                    if ".." in info.filename or info.filename.startswith("/"):
                        raise HTTPException(status_code=400, detail="Malicious path in ZIP detected")
                        
            # Reset file cursor for DataProcessor
            file.file.seek(0)
            
        except zipfile.BadZipFile:
             raise HTTPException(status_code=400, detail="Invalid ZIP file")

        movies_data, errors = await DataProcessor.process_zip_export(file)
        
        if not movies_data:
            raise HTTPException(
                status_code=400,
                detail="No valid movies found in ZIP export"
            )
        
        # Ensure user exists
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            # Create default user
            user = User(id=user_id, username=f"user_{user_id}")
            db.add(user)
            try:
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error(f"DB commit failed creating default user: {e}")
                raise
        
        # v1.1: Strict Onboarding - Require linked Letterboxd username
        if not user.letterboxd_username:
            raise HTTPException(
                status_code=400, 
                detail="Please link your Letterboxd username before uploading."
            )
        
        # v1.1: Create task for progress tracking
        task_store = get_task_store()
        task_id = task_store.generate_task_id()
        await task_store.create_task(task_id, 100, "Preparing upload...", user_id=user_id)
        
        # Process in background to avoid timeout
        background_tasks.add_task(
            enrich_movies_background,
            movies_data,
            user_id,
            task_id
        )
        
        return {
            "status": "processing",
            "message": f"Processing {len(movies_data)} movies from export",
            "movies_processed": len(movies_data),
            "movies_enriched": 0,
            "errors": errors[:10],
            "task_id": task_id  # v1.1: Return task_id for progress polling
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail="Upload processing failed")
