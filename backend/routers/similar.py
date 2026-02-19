"""
Get similar movie recommendations based on a specific movie
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
import logging

from config import get_db
from models.database import Movie
from services.qdrant_service import QdrantService
from services.tmdb_client import TMDBClient
from services.embedding_service import EmbeddingService
from dependencies import get_tmdb_client, get_qdrant_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/similar/{tmdb_id}")
async def get_similar_movies(
    tmdb_id: int,
    user_id: int = Query(..., description="User ID for personalization"),
    limit: int  = Query(12, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service)
):
    """
    Get movies similar to the specified movie.
    1. Try to find movie locally.
    2. If not found, fetch from TMDB and auto-populate.
    3. Use vector search for recommendations.
    4. Fallback to TMDB recommendations if local results are scarce.
    """
    try:
        embedding_service = EmbeddingService()
        
        # 1. Get movie details (Local or TMDB) to generate a FRESH query vector
        # We want to search by CONTENT (overview, genres), not by Title.
        # So we regenerate the embedding excluding the title.
        
        movie_title = "Unknown"
        overview = ""
        genres = []
        keywords = []
        
        # Try local first
        result = await db.execute(
            select(Movie).where(Movie.tmdb_id == tmdb_id)
        )
        source_movie = result.scalar_one_or_none()
        
        if source_movie:
            movie_title = source_movie.title
            overview = source_movie.overview
            genres = source_movie.genres
            # We might need keywords, but let's start with what we have
        else:
            # Fetch from TMDB via MovieService (Ensures full enrichment)
            logger.info(f"Movie {tmdb_id} not found locally, fetching via MovieService")
            from services.movie_service import MovieService
            movie_service = MovieService(db)
            
            source_movie = await movie_service.get_or_create_movie(tmdb_id)
            
            if not source_movie:
                raise HTTPException(status_code=404, detail="Movie not found in TMDB")
            
            movie_title = source_movie.title
            overview = source_movie.overview
            genres = source_movie.genres
            # Keywords are fetched inside MovieService, but we need them for the query vector below
            # Since we just ingested it, we could fetch them again or trust the embedding service to handle it if we passed the movie object?
            # Actually, MovieService generates the embedding for storage.
            # Here we need to generate a QUERY vector (without title).
            # We can fetch keywords from TMDB again or just use what we have.
            keywords = await tmdb.get_movie_keywords(tmdb_id)

        # Generate QUERY vector - WITHOUT title to avoid name-matching
        query_vector = embedding_service.generate_embedding({
            "title": movie_title,
            "overview": overview,
            "genres": genres,
            "keywords": keywords
        }, include_title=False).tolist()

        # 3. Search Qdrant
        similar_results = await qdrant.search_similar(
            query_vector=query_vector,
            limit=limit * 2,
            score_threshold=0.45  # Lowered threshold, but stricter content matching
        )
        
        recommendations = []
        seen_ids = {tmdb_id} # Exclude source movie
        
        # Process local results
        # Process local results
        # Batch fetch from DB to ensure fresh metadata (especially VectorBox Score)
        similar_tmdb_ids = []
        for r in similar_results:
            metadata = r.get("metadata", {})
            r_tmdb_id = metadata.get("tmdb_id") or r["movie_id"]
            if r_tmdb_id not in seen_ids:
                similar_tmdb_ids.append(r_tmdb_id)
                seen_ids.add(r_tmdb_id)

        # Fetch from DB
        db_movies_map = {}
        if similar_tmdb_ids:
            stmt = select(Movie).where(Movie.tmdb_id.in_(similar_tmdb_ids))
            result = await db.execute(stmt)
            for m in result.scalars().all():
                db_movies_map[m.tmdb_id] = m

        for r in similar_results:
            metadata = r.get("metadata", {})
            r_tmdb_id = metadata.get("tmdb_id") or r["movie_id"]
            
            # Skip if already processed in the loop above (it was added to seen_ids)
            # Wait, we need to preserve order of similar_results
            # We used seen_ids to filter duplicates, but we need to iterate similar_results again
            
            # Let's rebuild the loop correctly
            pass 

        # Re-iterate to build recommendations in order
        seen_ids_final = {tmdb_id} # Reset seen_ids for final list
        
        for r in similar_results:
            metadata = r.get("metadata", {})
            r_tmdb_id = int(metadata.get("tmdb_id") or r["movie_id"])
            
            if r_tmdb_id in seen_ids_final:
                continue
            seen_ids_final.add(r_tmdb_id)
            
            movie = db_movies_map.get(r_tmdb_id)
            
            if movie:
                # Use DB data
                recommendations.append({
                    "movie_id": movie.tmdb_id,
                    "title": movie.title,
                    "poster_path": movie.poster_path,
                    "year": movie.year,
                    "similarity_score": min(round(r["score"] * 100), 100),
                    "streaming_providers": [], # Enriched later
                    "overview": movie.overview,
                    "vote_average": movie.vote_average,
                    # Phase 12 Fields
                    "vectorbox_score": movie.vectorbox_score,
                    "imdb_rating": movie.imdb_rating,
                    "metacritic_rating": movie.metacritic_rating,
                    "rotten_tomatoes_rating": movie.rotten_tomatoes_rating,
                    "title_es": movie.title_es,
                    "overview_es": movie.overview_es
                })
            else:
                # Fallback to Metadata (if movie not in DB for some reason)
                poster_path = metadata.get("poster_path")
                if not poster_path:
                    try:
                        details = await tmdb.get_movie_details(r_tmdb_id)
                        if details: poster_path = details.get("poster_path")
                    except Exception as e:
                        logger.warning(f"Poster fetch failed for movie {r_tmdb_id}: {e}")
                    
                recommendations.append({
                    "movie_id": r_tmdb_id,
                    "title": metadata.get("title", "Unknown"),
                    "poster_path": poster_path,
                    "year": metadata.get("year"),
                    "similarity_score": min(round(r["score"] * 100), 100),
                    "streaming_providers": [],
                    "overview": metadata.get("overview", ""),
                    "vote_average": metadata.get("vote_average"),
                    "vectorbox_score": metadata.get("vectorbox_score"),
                    "imdb_rating": metadata.get("imdb_rating"),
                    "metacritic_rating": metadata.get("metacritic_rating"),
                    "rotten_tomatoes_rating": metadata.get("rotten_tomatoes_rating"),
                    "title_es": metadata.get("title_es"),
                    "overview_es": metadata.get("overview_es")
                })
            
        # 4. Fallback/Augment with TMDB Recommendations if few results
        # 4. Fallback/Augment with TMDB Recommendations if few results
        if len(recommendations) < 12:
            logger.info(f"Few local results ({len(recommendations)}), fetching TMDB recommendations")
            tmdb_recs = await tmdb._make_request(f"/movie/{tmdb_id}/recommendations")
            
            if tmdb_recs and tmdb_recs.get("results"):
                # Collect IDs to check against DB
                tmdb_results = tmdb_recs["results"][:12]
                tmdb_ids_to_check = [r["id"] for r in tmdb_results]
                
                # Batch fetch existing movies from DB
                existing_movies_map = {}
                if tmdb_ids_to_check:
                    result = await db.execute(select(Movie).where(Movie.tmdb_id.in_(tmdb_ids_to_check)))
                    for m in result.scalars().all():
                        existing_movies_map[m.tmdb_id] = m
                        
                logger.info(f"Enrichment: Found {len(existing_movies_map)} local movies out of {len(tmdb_ids_to_check)} TMDB results")
                
                for rec in tmdb_results:
                    rec_id = rec["id"]
                    if rec_id in seen_ids:
                        continue
                    
                    # Check if we have it locally
                    local_movie = existing_movies_map.get(rec_id)
                    
                    if local_movie:
                        logger.info(f"Enrichment: Using local movie for {rec_id} ({local_movie.title}). VB Score: {local_movie.vectorbox_score}")
                    else:
                        # Ingest on the fly to get full metadata (VectorBox Score, etc.)
                        logger.info(f"Enrichment: Ingesting movie {rec_id} on the fly.")
                        try:
                            from services.movie_service import MovieService
                            movie_service = MovieService(db)
                            local_movie = await movie_service.get_or_create_movie(rec_id)
                        except Exception as e:
                            logger.error(f"Failed to ingest movie {rec_id} on the fly: {e}")
                            local_movie = None
                    
                    recommendations.append({
                        "movie_id": rec_id,
                        "title": local_movie.title if local_movie else rec.get("title"),
                        "poster_path": local_movie.poster_path if local_movie else rec.get("poster_path"),
                        "year": local_movie.year if local_movie else (int(rec["release_date"][:4]) if rec.get("release_date") else None),
                        "similarity_score": 85, # Artificial score
                        "streaming_providers": [],
                        "overview": local_movie.overview if local_movie else rec.get("overview", ""),
                        "vote_average": local_movie.vote_average if local_movie else rec.get("vote_average"),
                        "vectorbox_score": local_movie.vectorbox_score if local_movie else None,
                        "imdb_rating": local_movie.imdb_rating if local_movie else None,
                        "metacritic_rating": local_movie.metacritic_rating if local_movie else None,
                        "rotten_tomatoes_rating": local_movie.rotten_tomatoes_rating if local_movie else None,
                        "title_es": local_movie.title_es if local_movie else None,
                        "overview_es": local_movie.overview_es if local_movie else None
                    })
                    seen_ids.add(rec_id)
                    
                    if len(recommendations) >= limit:
                        break
        
        # Limit results
        recommendations = recommendations[:limit]
        
        # Enrich with streaming info
        import asyncio
        
        async def fetch_providers(movie):
            providers = await tmdb.get_movie_watch_providers(movie["movie_id"], country_code="ES") # Default to ES or pass from request
            if providers:
                movie["streaming_providers"] = [p["provider_name"] for p in providers.get("flatrate", [])][:3]
            else:
                movie["streaming_providers"] = []
            return movie

        recommendations = await asyncio.gather(*[fetch_providers(m) for m in recommendations])
        
        # Singleton TMDBClient lifecycle managed by dependencies.close_services()
        
        return {
            "source_movie": {
                "tmdb_id": tmdb_id,
                "title": movie_title,
            },
            "recommendations": recommendations
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get similar movies for {tmdb_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to get similar movies")
