"""
Upload router for CSV file processing
Security: File validation, rate limiting, background tasks
"""
from fastapi import APIRouter, UploadFile, File, Depends, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from slowapi import Limiter
from slowapi.util import get_remote_address
import logging

from config import get_db
from services.tmdb_client import TMDBClient
from services.embedding_service import EmbeddingService
from services.qdrant_service import QdrantService
from services.clustering_service import ClusteringService
from services.provider_service import ProviderService
from services.omdb_client import OMDbClient
from services.data_processor import DataProcessor
from models.database import User, Movie, UserRating
from models.schemas import CSVUploadResponse

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

# In-memory status tracking (Simple for single instance)
upload_status = {}

@router.get("/status/{user_id}")
async def get_upload_status(user_id: int):
    """Get current upload status for user"""
    return upload_status.get(user_id, {"status": "idle", "message": "", "progress": 0})

async def enrich_movies_background(
    movies_data: list,
    user_id: int,
    db: AsyncSession
):
    """
    Background task to enrich movies with TMDB data and generate embeddings
    """
    tmdb = TMDBClient()
    omdb = OMDbClient()
    embedding_service = EmbeddingService()
    qdrant = QdrantService()
    provider_service = ProviderService(db, tmdb)
    
    enriched_count = 0
    total_movies = len(movies_data)
    
    # Update status
    upload_status[user_id] = {
        "status": "processing",
        "message": "Starting enrichment...",
        "progress": 0,
        "total": total_movies,
        "current": 0
    }
    
    try:
        for i, movie_data in enumerate(movies_data):
            existing_movie = None
            try:
                # Update progress every 5 movies
                if i % 5 == 0:
                    upload_status[user_id] = {
                        "status": "processing",
                        "message": f"Enriching movie {i+1}/{total_movies}",
                        "progress": int((i / total_movies) * 100),
                        "total": total_movies,
                        "current": i + 1
                    }
                
                if not existing_movie:
                    # Search TMDB
                    tmdb_result = await tmdb.search_movie(
                        movie_data["title"],
                        movie_data.get("year")
                    )
                    
                    if tmdb_result:
                        tmdb_id = tmdb_result["id"]
                        
                        # CRITICAL: Check if this TMDB ID exists in DB (even if title/year didn't match exactly)
                        result = await db.execute(select(Movie).where(Movie.tmdb_id == tmdb_id))
                        existing_movie_by_tmdb = result.scalar_one_or_none()

                        if existing_movie_by_tmdb:
                            existing_movie = existing_movie_by_tmdb
                        else:
                            # Use MovieService to ingest (with full enrichment)
                            # We initialize it here to use the shared DB session
                            from services.movie_service import MovieService
                            movie_service = MovieService(db)
                            
                            new_movie = await movie_service.ingest_movie(
                                tmdb_id=tmdb_id,
                                letterboxd_uri=movie_data.get("letterboxd_uri")
                            )
                            
                            
                            if new_movie:
                                existing_movie = new_movie
                                enriched_count += 1
                        
                        # Self-Heal: Ensure existing movie has keywords/vector (Phase 13)
                        if existing_movie:
                             from services.movie_service import MovieService
                             # Note: instantiating service inside loop might be heavy if not reused, 
                             # but here we need it occasionally. Better to init outside if frequent.
                             # But existing_movie logic is nested.
                             # Re-using previous import if available.
                             if 'movie_service' not in locals():
                                 from services.movie_service import MovieService
                                 movie_service = MovieService(db)
                                 
                             await movie_service.enrich_movie(existing_movie)
                
                # Create or Update UserRating record
                if existing_movie:
                    # Check if rating already exists
                    result = await db.execute(
                        select(UserRating).where(
                            UserRating.user_id == user_id,
                            UserRating.movie_id == existing_movie.id
                        )
                    )
                    existing_rating = result.scalar_one_or_none()
                    
                    if existing_rating:
                        # Update existing record with new flags if present
                        if movie_data.get("rating"):
                            existing_rating.rating = movie_data["rating"]
                        if movie_data.get("is_watchlist"):
                            existing_rating.is_watchlist = True
                        if movie_data.get("is_liked"):
                            existing_rating.is_liked = True
                        if movie_data.get("is_watched"):
                            existing_rating.is_watched = True
                        if movie_data.get("watched_date"):
                            existing_rating.watched_date = movie_data["watched_date"]
                        if movie_data.get("review"):
                            existing_rating.review = movie_data["review"]
                    else:
                        # Create new record
                        rating = UserRating(
                            user_id=user_id,
                            movie_id=existing_movie.id,
                            rating=movie_data.get("rating"),
                            is_watchlist=movie_data.get("is_watchlist", False),
                            is_liked=movie_data.get("is_liked", False),
                            is_watched=movie_data.get("is_watched", False),
                            watched_date=movie_data.get("watched_date"),
                            review=movie_data.get("review")
                        )
                        db.add(rating)
                
                await db.commit()
                
            except Exception as e:
                logger.error(f"Error enriching movie {movie_data.get('title')}: {e}")
                await db.rollback()
                continue
        
        # After all movies processed, create clusters
        try:
            clustering = ClusteringService()
            await clustering.create_user_clusters(user_id, db)
            logger.info(f"Created clusters for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to create clusters: {e}")
            
        # Mark as complete
        upload_status[user_id] = {
            "status": "completed",
            "message": "Upload complete!",
            "progress": 100
        }
        
    finally:
        await tmdb.close()


@router.post("/export", response_model=CSVUploadResponse)
@limiter.limit("5/minute")  # Security: Rate limit uploads
async def upload_export(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: int = 1,  # TODO: Get from auth token
    db: AsyncSession = Depends(get_db)
):
    """
    Upload Letterboxd export ZIP file.
    Parses ratings, watchlist, likes, and watched history.
    """
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
            await db.commit()
        
        # Process in background to avoid timeout
        background_tasks.add_task(
            enrich_movies_background,
            movies_data,
            user_id,
            db
        )
        
        return CSVUploadResponse(
            status="processing",
            message=f"Processing {len(movies_data)} movies from export",
            movies_processed=len(movies_data),
            movies_enriched=0,
            errors=errors[:10]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail="Upload processing failed")
