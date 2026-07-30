"""
Get similar movie recommendations based on a specific movie
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
import asyncio
import logging
import numpy as np

from config import get_db
from limiter import limiter
from models.database import Movie
from services.qdrant_service import QdrantService
from services.tmdb_client import TMDBClient
from services.embedding_service import EmbeddingService
from dependencies import get_tmdb_client, get_qdrant_service, get_optional_current_user, get_embedding_service

from models.schemas import TokenResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# Both endpoints below quality-gate the Qdrant hits in Postgres, on columns that
# are not in the vector payload (VBS ≥ 55, ≥ 100 votes, a poster). Only 38.9% of
# the catalogue clears that gate, and a film's neighbours are correlated with it
# — an obscure film has obscure neighbours — so the survivors of a narrow fetch
# are far fewer than the average would suggest.
#
# Measured 2026-07-29 over 40 random catalogue films asking for 12 similars:
#
#   fetch  limit×2 (24)   8.6 of 12 on average · 12 of 40 shelves full · 1 empty
#   fetch  limit×8 (96)  11.9 of 12 on average · 39 of 40 shelves full · 0 empty
#
# So the rail was quietly a third short most of the time. Over-fetching is the
# whole fix here: score_threshold still decides what is genuinely similar, and a
# film with few real neighbours correctly returns few.
QUALITY_GATE_OVERFETCH = 8

# The gate's vote floor was 100, which is not a quality signal — it is a fame
# signal, and it was deleting the best neighbours. Measured 2026-07-31 on "The 47"
# (tmdb 1174481): the 5 nearest neighbours (0.776..0.752 — Gloria Mundi, Nasir,
# Crows and Sparrows, Give Me Liberty, My Brother's Wedding) all cleared VBS≥55
# and had posters, and all died on votes (99, 4, 9, 66, 21). What survived to the
# top of the rail instead was Good Will Hunting.
#
# Across the catalogue the floor of 100 drops 4,535 films that already clear
# VBS≥55 + poster, to catch 5 with an empty overview. VBS≥55 is NULL-false, so
# the phantom stubs the gate was written for (VBS=None, no poster) are already
# excluded by the other two clauses. 20 keeps the tail honest without charging
# arthouse for being arthouse.
MIN_VOTE_COUNT = 20


async def _ingest_similar_background(tmdb_ids: List[int], tmdb: TMDBClient) -> None:
    """Background ingest of TMDB recommendations missing from local DB.

    Owns its own AsyncSession. Per-id try/except, never re-raises.
    """
    if not tmdb_ids:
        return
    from config import AsyncSessionLocal
    from services.movie_service import MovieService
    async with AsyncSessionLocal() as db:
        movie_service = MovieService(db, tmdb=tmdb)
        try:
            for tid in tmdb_ids:
                try:
                    await movie_service.get_or_create_movie(tid)
                except Exception as e:
                    logger.error(f"[similar/background] Ingest failed for tmdb_id={tid}: {e}")
        finally:
            # Releases the lazily-created OMDb/Qdrant clients; tmdb is the
            # injected singleton and is not closed by MovieService.close().
            await movie_service.close()


@router.get("/similar/{tmdb_id}")
@limiter.limit("20/minute")
async def get_similar_movies(
    request: Request,
    tmdb_id: int,
    background_tasks: BackgroundTasks,
    limit: int = Query(12, ge=1, le=50),
    current_user: Optional[TokenResponse] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding_service: EmbeddingService = Depends(get_embedding_service)
):
    """
    Get movies similar to the specified movie.
    1. Try to find movie locally.
    2. If not found, fetch from TMDB and auto-populate.
    3. Use vector search for recommendations.
    4. Fallback to TMDB recommendations if local results are scarce.
    """
    try:
        
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
            movie_service = MovieService(db, tmdb=tmdb)

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

        # Prefer the stored Qdrant vector — built from cinematic_description, no title.
        # Fresh on-the-fly encoding from overview+genres+keywords lives in a different
        # vector space than the catalogue and produces off-theme neighbours.
        query_vector = await qdrant.get_vector(tmdb_id)

        if not query_vector:
            text_override = source_movie.cinematic_description if source_movie.cinematic_description else None
            loop = asyncio.get_running_loop()
            query_vector = await loop.run_in_executor(
                None,
                lambda: embedding_service.generate_embedding({
                    "title": movie_title,
                    "overview": overview,
                    "genres": genres,
                    "keywords": keywords,
                }, text_override=text_override).tolist()
            )

        # 3. Search Qdrant
        similar_results = await qdrant.search_similar(
            query_vector=query_vector,
            limit=limit * QUALITY_GATE_OVERFETCH,
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
            # Quality gate — mirror /similar/multi. Without it, metadata-less
            # phantom entries (empty overview → hallucinated embedding, VBS=None,
            # 0 votes, no poster) surface as top neighbours (a duplicate "Wolf
            # Totem" stub scored 0.81 to Nausicaä). Gated-out films fall out of
            # this map and are skipped in the build loop below.
            stmt = (
                select(Movie)
                .where(Movie.tmdb_id.in_(similar_tmdb_ids))
                .where(Movie.vectorbox_score >= 55)
                .where(Movie.vote_count >= MIN_VOTE_COUNT)
                .where(Movie.poster_path.isnot(None))
            )
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

            # Not in the gated map = failed the quality gate (phantom/low-quality)
            # or not a catalogue film → skip. The enriched-vector gate means real
            # neighbours are always in the DB, so no legitimate result is lost.
            if not movie:
                continue

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

                "title_es": movie.title_es,
                "overview_es": movie.overview_es
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

                to_ingest_background: List[int] = []
                for rec in tmdb_results:
                    rec_id = rec["id"]
                    if rec_id in seen_ids:
                        continue

                    # Check if we have it locally
                    local_movie = existing_movies_map.get(rec_id)

                    if local_movie:
                        logger.info(f"Enrichment: Using local movie for {rec_id} ({local_movie.title}). VB Score: {local_movie.vectorbox_score}")
                    else:
                        # Defer ingest to background — return TMDB payload now, enrich next request
                        to_ingest_background.append(rec_id)

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

                        "title_es": local_movie.title_es if local_movie else None,
                        "overview_es": local_movie.overview_es if local_movie else None
                    })
                    seen_ids.add(rec_id)

                    if len(recommendations) >= limit:
                        break

                if to_ingest_background:
                    background_tasks.add_task(_ingest_similar_background, to_ingest_background, tmdb)
        
        # Limit results
        recommendations = recommendations[:limit]
        
        # Enrich with streaming info
        import asyncio
        
        async def fetch_providers(movie):
            providers = await tmdb.get_watch_providers(movie["movie_id"], country="ES") # Default to ES or pass from request
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


# ---------------------------------------------------------------------------
# POST /similar/multi — Centroid-based MLT for up to 5 seed movies (public)
# ---------------------------------------------------------------------------

class MultiSeedRequest(BaseModel):
    tmdb_ids: List[int] = Field(..., min_length=1, max_length=5)
    limit: int = Field(12, ge=1, le=50)
    country_code: str = "ES"


@router.post("/similar/multi")
@limiter.limit("20/minute")
async def get_similar_multi(
    request: Request,
    body: MultiSeedRequest,
    current_user: Optional[TokenResponse] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
    qdrant: QdrantService = Depends(get_qdrant_service),
    tmdb: TMDBClient = Depends(get_tmdb_client),
):
    """
    Find movies similar to up to 5 seed movies by averaging their stored
    Qdrant vectors and searching with the centroid. Public — works for guests.
    """
    seed_ids = list(dict.fromkeys(body.tmdb_ids))  # preserve order, dedupe

    vectors_map = await qdrant.get_vectors_batch(seed_ids)
    
    # Auto-ingest missing vectors
    missing_ids = [tid for tid in seed_ids if not vectors_map.get(tid)]
    if missing_ids:
        from services.movie_service import MovieService
        movie_service = MovieService(db, tmdb=tmdb)
        for tid in missing_ids:
            try:
                await movie_service.get_or_create_movie(tid)
            except Exception as e:
                logger.error(f"Failed to ingest missing seed movie {tid}: {e}")
        
        # Fetch newly generated vectors
        new_vectors_map = await qdrant.get_vectors_batch(missing_ids)
        vectors_map.update(new_vectors_map)

    vectors = [np.array(v) for v in vectors_map.values() if v]
    if not vectors:
        raise HTTPException(status_code=404, detail="No vectors found for provided movies")

    centroid = np.mean(vectors, axis=0).tolist()

    # Over-fetch to absorb seed exclusions and quality-gate filtering. The +20
    # covered the seeds but not the gate, which drops ~61% of what it sees.
    raw = await qdrant.search_similar(
        query_vector=centroid,
        limit=body.limit * QUALITY_GATE_OVERFETCH + len(seed_ids),
        score_threshold=0.45,
    )

    seed_set = set(seed_ids)
    ordered_ids: List[int] = []
    score_by_id: dict = {}
    for r in raw:
        meta = r.get("metadata", {}) or {}
        rid = int(meta.get("tmdb_id") or r["movie_id"])
        if rid in seed_set or rid in score_by_id:
            continue
        ordered_ids.append(rid)
        score_by_id[rid] = r["score"]

    # Quality-gated DB fetch
    movies_q = await db.execute(
        select(Movie)
        .where(Movie.tmdb_id.in_(ordered_ids))
        .where(Movie.vectorbox_score >= 55)
        .where(Movie.vote_count >= MIN_VOTE_COUNT)
        .where(Movie.poster_path.isnot(None))
    )
    by_id = {m.tmdb_id: m for m in movies_q.scalars().all()}

    seeds_q = await db.execute(select(Movie).where(Movie.tmdb_id.in_(seed_ids)))
    seed_titles = {m.tmdb_id: m.title for m in seeds_q.scalars().all()}

    recs = []
    for rid in ordered_ids:
        m = by_id.get(rid)
        if not m:
            continue
        recs.append({
            "movie_id": m.tmdb_id,
            "title": m.title,
            "poster_path": m.poster_path,
            "year": m.year,
            "similarity_score": min(round(score_by_id[rid] * 100), 100),
            "streaming_providers": [],
            "overview": m.overview,
            "vote_average": m.vote_average,
            "vectorbox_score": m.vectorbox_score,
            "imdb_rating": m.imdb_rating,
            "metacritic_rating": m.metacritic_rating,

            "title_es": m.title_es,
            "overview_es": m.overview_es,
        })
        if len(recs) >= body.limit:
            break

    return {
        "source_movies": [
            {"tmdb_id": tid, "title": seed_titles.get(tid, "Unknown")}
            for tid in seed_ids
        ],
        "recommendations": recs,
    }
