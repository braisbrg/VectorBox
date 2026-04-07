from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import asyncio
from difflib import SequenceMatcher

from config import get_db
from dependencies import get_tmdb_client, get_qdrant_service, get_embedding_service, get_current_user
from models.schemas import TokenResponse
from services.nlp_search import parse_user_intent, search_with_reasoning
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService
from services.tmdb_client import TMDBClient
from services.provider_service import ProviderService
from models.database import UserRating, Movie
from sqlalchemy import select, or_

logger = logging.getLogger(__name__)
router = APIRouter()

class SearchRequest(BaseModel):
    query: str
    use_deep_analysis: Optional[bool] = False
    country_code: Optional[str] = "ES"

class SearchResponse(BaseModel):
    results: List[dict]
    intent: dict

def filter_es_providers(all_providers: List[str]) -> List[str]:
    """Pure function to filter provider names against the ES whitelist."""
    es_whitelist = {"Netflix", "Amazon Prime Video", "HBO Max", "Disney+", "Apple TV", "Movistar+", "Filmin"}
    return [p for p in all_providers if p in es_whitelist]


async def _item_to_item_search(
    movie_id: int,
    movie_title: str,
    qdrant: QdrantService,
    tmdb: TMDBClient,
) -> Optional[SearchResponse]:
    """Shared helper for Item-to-Item recommendation (deduplicated)."""
    vector = await qdrant.get_vector(movie_id)
    if not vector:
        return None
    raw_results = await qdrant.search_similar(
        query_vector=vector,
        limit=20,
        score_threshold=0.4,
        filters={"exclude_tmdb_ids": [movie_id]}
    )
    results = []
    for r in raw_results:
        metadata = r.get("metadata", {})
        # Normalized scoring (same formula as Tier 1 semantic search)
        min_sim, max_sim = 0.2, 0.7
        raw_score = r["score"]
        if raw_score > max_sim:
            final_score = min(99, 90 + ((raw_score - max_sim) * 100))
        else:
            normalized = max(0.0, min(1.0,
                (raw_score - min_sim) / (max_sim - min_sim)))
            final_score = 60 + (normalized * 30)
        results.append({
            "movie_id": metadata.get("tmdb_id") or r["movie_id"],
            "title": metadata.get("title", "Unknown"),
            "overview": metadata.get("overview", ""),
            "poster_path": metadata.get("poster_path"),
            "score": round(final_score, 0),
            "year": metadata.get("year"),
            "runtime": metadata.get("runtime"),
            "genres": metadata.get("genres", []),
            "vote_average": metadata.get("vote_average"),
        })
    return SearchResponse(
        results=results,
        intent={
            "semantic_query": f"Movies like {movie_title}",
            "reasoning": f"Showing movies similar to '{movie_title}'."
        }
    )


# Re-implementing with proper decorator injection
from limiter import limiter

@router.post("/natural", response_model=SearchResponse)
@limiter.limit("5/minute")
async def natural_language_search(
    request: Request, # Request object is required for slowapi
    search_req: SearchRequest,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding_service: EmbeddingService = Depends(get_embedding_service)
):
    """
    Advanced natural language search with semantic expansion and vibe filtering.
    Handles complex queries like "old gangster movie", "90s hidden gem", "short anime".
    Also handles "Movies like X" by detecting title matches.
    """
    try:
        # 0. Check if query is a specific movie title (Item-to-Item Search)
        potential_movie_id = None
        potential_movie_title = None
        
        # Search local DB first for exact match
        exact_match = await db.execute(
            select(Movie).where(Movie.title.ilike(search_req.query))
        )
        local_movie = exact_match.scalars().first()
        
        if local_movie:
            potential_movie_id = local_movie.tmdb_id
            potential_movie_title = local_movie.title
            logger.info(f"Found exact local title match: {local_movie.title}")
        else:
            # Search TMDB
            tmdb_results = await tmdb._make_request("/search/movie", {"query": search_req.query})
            if tmdb_results and tmdb_results.get("results"):
                top_match = tmdb_results["results"][0]
                # Check if it's a good match (exact title or very close)
                if top_match["title"].lower() == search_req.query.lower():
                    potential_movie_id = top_match["id"]
                    potential_movie_title = top_match["title"]
                    logger.info(f"Found exact TMDB title match: {top_match['title']}")
        
        # If we found a specific movie, perform Item-to-Item recommendation
        if potential_movie_id:
            logger.info(f"Switching to Item-to-Item search based on movie: {potential_movie_title}")
            result = await _item_to_item_search(
                potential_movie_id, potential_movie_title, qdrant, tmdb
            )
            if result:
                return result

        # 1. Parse Intent with Advanced LLM (Fallback to Semantic Search)
        intent = await parse_user_intent(search_req.query)
        logger.info(f"Parsed intent: {intent}")
        logger.info(f"Reasoning: {intent.reasoning}")
        
        # Check for Reference Movie (e.g. "movies like Inception")
        if intent.reference_movie:
            logger.info(f"Detected reference movie in intent: {intent.reference_movie}")
            # Try to find this movie
            ref_movie_match = await db.execute(
                select(Movie).where(Movie.title.ilike(intent.reference_movie))
            )
            ref_movie = ref_movie_match.scalars().first()
            
            if ref_movie:
                potential_movie_id = ref_movie.tmdb_id
                potential_movie_title = ref_movie.title
            else:
                # Try TMDB
                tmdb_ref = await tmdb.search_movie(intent.reference_movie)
                if tmdb_ref:
                    potential_movie_id = tmdb_ref["id"]
                    potential_movie_title = tmdb_ref["title"]
                    
            if potential_movie_id:
                logger.info(f"Performing Item-to-Item search for reference: {potential_movie_title}")
                result = await _item_to_item_search(
                    potential_movie_id, potential_movie_title, qdrant, tmdb
                )
                if result:
                    return result
        
        # 2. Generate Embedding for the EXPANDED semantic query
        loop = asyncio.get_event_loop()
        query_vector = await loop.run_in_executor(
            None,
            lambda: embedding_service.generate_embedding({
                "title": intent.semantic_query,
                "overview": intent.semantic_query,
                "genres": intent.include_genres or [],
                "keywords": []
            }).tolist()
        )
        
        # 3. Construct Advanced Qdrant Filters
        qdrant_filters = {}
        
        # Include genres (any of these)
        if intent.include_genres:
            qdrant_filters["include_genres"] = intent.include_genres
            
        # Year range
        if intent.year_min:
            qdrant_filters["year_min"] = intent.year_min
        if intent.year_max:
            qdrant_filters["year_max"] = intent.year_max
        
        # Runtime constraint
        if intent.max_runtime_minutes:
            qdrant_filters["max_runtime"] = intent.max_runtime_minutes
        
        # Popularity vibe (hidden_gem, blockbuster, any)
        if intent.popularity_vibe == "blockbuster":
            qdrant_filters["min_vote_count"] = 3000
        elif intent.popularity_vibe == "hidden_gem":
            qdrant_filters["max_vote_count"] = 1000
            
        # Language filter
        if intent.original_language:
            qdrant_filters["original_language"] = intent.original_language
            
        # 3.5. Exclude Watched Movies
        # Fetch user's rated/liked movies to exclude them
        result = await db.execute(
            select(Movie.tmdb_id)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(UserRating.user_id == current_user.user_id)
            .where(or_(UserRating.rating.isnot(None), UserRating.is_liked.is_(True)))
        )
        watched_tmdb_ids = [row[0] for row in result.all() if row[0] is not None]
        
        if watched_tmdb_ids:
            qdrant_filters["exclude_tmdb_ids"] = watched_tmdb_ids
            
        # 4. Search Qdrant with Advanced Filters
        raw_results = await qdrant.search_similar(
            query_vector=query_vector,
            limit=20,
            score_threshold=0.3, # Semantic search standard
            filters=qdrant_filters
        )
        
        logger.info(f"Qdrant returned {len(raw_results)} results")
        
        # 5. Transform results for frontend
        results = []
        
        # Collect IDs to fetch from DB
        tmdb_ids = []
        for r in raw_results:
            metadata = r.get("metadata", {})
            tmdb_id = metadata.get("tmdb_id") or r["movie_id"]
            if tmdb_id:
                tmdb_ids.append(int(tmdb_id))
        
        # Fetch from DB
        db_movies = {}
        if tmdb_ids:
            stmt = select(Movie).where(Movie.tmdb_id.in_(tmdb_ids))
            db_res = await db.execute(stmt)
            for m in db_res.scalars().all():
                db_movies[m.tmdb_id] = m

        missing_details_ids = []
        for r in raw_results:
            metadata = r.get("metadata", {})
            tmdb_id = metadata.get("tmdb_id") or r["movie_id"]
            if (not metadata.get("poster_path") or not metadata.get("overview")) and tmdb_id:
                missing_details_ids.append(tmdb_id)
            
        tmdb_details_map = {}
        if missing_details_ids:
            tasks = [tmdb.get_movie_details(mid) for mid in missing_details_ids]
            results_details = await asyncio.gather(*tasks, return_exceptions=True)
            for tmdb_id, res in zip(missing_details_ids, results_details):
                if not isinstance(res, Exception) and res:
                    tmdb_details_map[tmdb_id] = res

        for r in raw_results:
            metadata = r.get("metadata", {})
            tmdb_id = metadata.get("tmdb_id") or r["movie_id"]
            poster_path = metadata.get("poster_path")
            
            # Enrich from DB if available
            db_movie = db_movies.get(int(tmdb_id)) if tmdb_id else None

            # Fix missing details (poster or overview)
            if (not poster_path or not metadata.get("overview")) and tmdb_id:
                details = tmdb_details_map.get(tmdb_id)
                if details:
                    if not poster_path:
                        poster_path = details.get("poster_path")
                    if not metadata.get("overview"):
                        metadata["overview"] = details.get("overview", "")

            # Normalize score
            min_sim = 0.2
            max_sim = 0.7
            raw_score = r["score"]
            
            if raw_score > max_sim:
                final_score = 90 + ((raw_score - max_sim) * 100)
                final_score = min(99, final_score)
            else:
                normalized = (raw_score - min_sim) / (max_sim - min_sim)
                normalized = max(0.0, min(1.0, normalized))
                final_score = 60 + (normalized * 30)

            # Title Match Boost (Weighted Average)
            # If query is very similar to title, blend the scores
            
            # Check similarity
            title_sim = SequenceMatcher(None, search_req.query.lower(), metadata.get("title", "").lower()).ratio()
            
            # If > 0.8 similarity, blend 50/50 with vector score
            if title_sim > 0.8:
                title_score = 90 + (title_sim * 9)
                # Blend: 50% Vector, 50% Title
                # This ensures semantic relevance still matters (avoiding "Avatar 1916" issue)
                # but boosts "Parasites" -> "Parasite" significantly.
                final_score = (final_score * 0.5) + (title_score * 0.5)
                logger.info(f"Blended score for {metadata.get('title')} (Sim: {title_sim:.2f}): {final_score}")

            # Imp 3: Dynamic quality gate — apply sigmoid weight after normalization
            vb_score = db_movie.vectorbox_score if db_movie else None
            if vb_score is not None:
                import math as _math
                if intent.quality_gate_bypass:
                    # Relaxed sigmoid for campy/trash/guilty-pleasure intent
                    midpoint, steepness = 25, 0.10
                else:
                    # Default quality gate
                    midpoint, steepness = 65, 0.15
                weight = 1.0 / (1.0 + _math.exp(-steepness * (vb_score - midpoint)))
                final_score = final_score * weight

            result = {
                "movie_id": tmdb_id,
                "title": metadata.get("title", "Unknown"),
                "overview": metadata.get("overview", ""),
                "poster_path": poster_path,
                "score": round(final_score, 0),
                "year": metadata.get("year"),
                "runtime": metadata.get("runtime"),
                "genres": metadata.get("genres", []),
                "vote_average": metadata.get("vote_average"),
                # Phase 12 Fields (from DB)
                "vectorbox_score": db_movie.vectorbox_score if db_movie else None,
                "imdb_rating": db_movie.imdb_rating if db_movie else None,
                "metacritic_rating": db_movie.metacritic_rating if db_movie else None,
                "rotten_tomatoes_rating": db_movie.rotten_tomatoes_rating if db_movie else None,
                "title_es": db_movie.title_es if db_movie else None,
                "overview_es": db_movie.overview_es if db_movie else None
            }
            results.append(result)
        
        # 6. Fetch Streaming Providers
        try:
            provider_service = ProviderService(db, tmdb)
            
            # Get IDs
            result_ids = [r["movie_id"] for r in results if r["movie_id"]]
            
            # Fetch batch (use country_code from request, fallback to ES)
            providers_map = await provider_service.get_providers_batch(
                result_ids, search_req.country_code or "ES"
            )
            
            es_whitelist = {"Netflix", "Amazon Prime Video", "HBO Max", "Disney+", "Apple TV", "Movistar+", "Filmin"}
            
            # Attach to results
            for r in results:
                mid = r["movie_id"]
                if mid in providers_map:
                    # Extract provider names
                    all_providers = [p["provider_name"] for p in providers_map[mid]]
                    r["streaming_providers"] = filter_es_providers(all_providers)
                else:
                    r["streaming_providers"] = []
                    
            
        except Exception as e:
            logger.error(f"Failed to fetch providers for search results: {e}")
            for r in results:
                r["streaming_providers"] = []

        # 7. Deep Analysis (Optional RAG Step)
        if search_req.use_deep_analysis and results:
            logger.info("Deep Analysis requested. Calling Tier 2 Intelligence...")
            try:
                # Pass results to Llama 70B
                reasoned_picks = await search_with_reasoning(search_req.query, results)
                
                if reasoned_picks:
                    # Re-rank: Keep only selected, map reasons
                    reasoned_map = {p.movie_id: p.ai_reason for p in reasoned_picks}
                    new_results = []
                    
                    for r in results:
                        mid = r["movie_id"]
                        if mid in reasoned_map:
                            r["ai_reason"] = reasoned_map[mid]
                            r["score"] = 100 # Boost score for AI selected
                            new_results.append(r)
                            
                    # If we have picks, return them. If LLM returned 0, fallback to original list.
                    if new_results:
                        logger.info(f"Deep Analysis curated {len(new_results)} items.")
                        results = new_results
                
            except Exception as e:
                logger.error(f"Deep Analysis failed (graceful fallback): {e}")

        return SearchResponse(
            results=results,
            intent=intent.model_dump()
        )
        
    except Exception as e:
        import traceback
        logger.error(f"Search failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Search service unavailable")

@router.get("/movies", response_model=SearchResponse)
async def search_movies(
    query: str,
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding_service: EmbeddingService = Depends(get_embedding_service)
):
    """
    Hybrid search:
    1. Search Qdrant for local matches
    2. If insufficient results, search TMDB
    3. Auto-populate Qdrant with new TMDB discoveries
    """
    try:
        
        # 1. Generate query vector
        loop = asyncio.get_event_loop()
        query_vector = await loop.run_in_executor(
            None,
            lambda: embedding_service.generate_embedding({
                "title": query,
                "overview": "",
                "genres": [],
                "keywords": []
            }).tolist()
        )
        
        # 2. Search Qdrant (Local)
        local_results = await qdrant.search_similar(
            query_vector=query_vector,
            limit=10,
            score_threshold=0.6 # High threshold for exact-ish matches
        )
        
        results = []
        seen_ids = set()
        
        # Process local results
        # Process local results
        
        # Collect IDs to fetch from DB
        tmdb_ids = []
        for r in local_results:
            metadata = r.get("metadata", {})
            movie_id = metadata.get("tmdb_id") or r["movie_id"]
            if movie_id:
                tmdb_ids.append(int(movie_id))
        
        # Fetch from DB
        db_movies = {}
        if tmdb_ids:
            stmt = select(Movie).where(Movie.tmdb_id.in_(tmdb_ids))
            db_res = await db.execute(stmt)
            for m in db_res.scalars().all():
                db_movies[m.tmdb_id] = m

        for r in local_results:
            metadata = r.get("metadata", {})
            # Use TMDB ID from metadata if available, otherwise fallback to internal ID (which might be wrong for external links)
            movie_id = metadata.get("tmdb_id") or r["movie_id"]
            if movie_id:
                seen_ids.add(int(movie_id))
            
            # Enrich from DB if available
            db_movie = db_movies.get(int(movie_id)) if movie_id else None

            # Title Match Boost (Weighted Average)
            title_sim = SequenceMatcher(None, query.lower(), metadata.get("title", "").lower()).ratio()
            
            final_score = min(round(r["score"] * 100), 100)
            
            if title_sim > 0.8:
                title_score = 90 + (title_sim * 9)
                final_score = (final_score * 0.5) + (title_score * 0.5)

            results.append({
                "movie_id": movie_id,
                "title": metadata.get("title", "Unknown"),
                "overview": metadata.get("overview", ""),
                "poster_path": metadata.get("poster_path"),
                "score": round(final_score, 0),
                "year": metadata.get("year"),
                "runtime": metadata.get("runtime"),
                "genres": metadata.get("genres", []),
                "vote_average": metadata.get("vote_average"),
                # Phase 12 Fields (from DB)
                "vectorbox_score": db_movie.vectorbox_score if db_movie else None,
                "imdb_rating": db_movie.imdb_rating if db_movie else None,
                "metacritic_rating": db_movie.metacritic_rating if db_movie else None,
                "rotten_tomatoes_rating": db_movie.rotten_tomatoes_rating if db_movie else None,
                "title_es": db_movie.title_es if db_movie else None,
                "overview_es": db_movie.overview_es if db_movie else None
            })
            
        # 3. Fallback to TMDB if few results
        if len(results) < 5:
            logger.info(f"Few local results ({len(results)}), searching TMDB for: {query}")
            
            try:
                # Search TMDB
                tmdb_results = await tmdb._make_request("/search/movie", {"query": query})
                
                if tmdb_results and tmdb_results.get("results"):
                    unseen_ids = [m["id"] for m in tmdb_results["results"][:5] if int(m["id"]) not in seen_ids]
                    
                    if unseen_ids:
                        detail_tasks = [tmdb.get_movie_details(mid) for mid in unseen_ids]
                        kw_tasks = [tmdb.get_movie_keywords(mid) for mid in unseen_ids]
                        
                        details_results = await asyncio.gather(*detail_tasks, return_exceptions=True)
                        kw_results = await asyncio.gather(*kw_tasks, return_exceptions=True)
                        
                        for tmdb_id, details, keywords_res in zip(unseen_ids, details_results, kw_results):
                            if isinstance(details, Exception) or not details:
                                continue
                            
                            keywords = keywords_res if not isinstance(keywords_res, Exception) else []
                            
                            # Extract metadata (FIX 3: now inside the for loop)
                            title = details.get("title")
                            overview = details.get("overview", "")
                            year = int(details["release_date"][:4]) if details.get("release_date") else None
                            genres = [g["name"] for g in details.get("genres", [])]
                        
                            # Generate embedding
                            loop = asyncio.get_event_loop()
                            vector = await loop.run_in_executor(
                                None,
                                lambda: embedding_service.generate_embedding({
                                    "title": title,
                                    "overview": overview,
                                    "genres": genres,
                                    "keywords": keywords
                                }).tolist()
                            )
                            
                            # Prepare metadata for Qdrant
                            payload = {
                                "title": title,
                                "overview": overview,
                                "year": year,
                                "runtime": details.get("runtime"),
                                "genres": genres,
                                "poster_path": details.get("poster_path"),
                                "vote_average": details.get("vote_average"),
                                "vote_count": details.get("vote_count"),
                                "tmdb_id": tmdb_id
                            }
                            
                            # Upsert to Qdrant (Fire & Forget / Async)
                            # Note: In production, consider background task
                            await qdrant.upsert_movie_vector(tmdb_id, vector, payload)
                            logger.info(f"Auto-populated movie: {title} ({tmdb_id})")
                            
                            # Add to results
                            results.append({
                                "movie_id": tmdb_id,
                                "title": title,
                                "overview": overview,
                                "poster_path": payload["poster_path"],
                                "score": 100 if query.lower() in title.lower() else 80, # Artificial score for exact matches
                                "year": year,
                                "runtime": payload["runtime"],
                                "genres": genres,
                                "vote_average": payload["vote_average"]
                            })
                        
            except Exception as e:
                logger.error(f"TMDB fallback failed: {e}")
        
        return SearchResponse(
            results=results,
            intent={"semantic_query": query}
        )
    except Exception as e:
        logger.error(f"Movie search failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search service unavailable")
