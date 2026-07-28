from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, constr
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import asyncio
from config import get_db
from dependencies import get_tmdb_client, get_qdrant_service, get_embedding_service, get_current_user, get_optional_current_user, get_redis
from models.schemas import TokenResponse
from services.nlp_search import parse_user_intent, search_with_reasoning, MovieSearchIntent
from services.magic_search_ranking import (
    compute_blended_score,
    intent_complexity,
    movie_passes_post_filter,
    should_run_deep_analysis,
    title_sim_score,
)
from services import showcase_service
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService
from services.tmdb_client import TMDBClient
from services.provider_service import ProviderService
from models.database import UserRating, Movie
from sqlalchemy import select, or_
from utils.scoring import normalize_similarity_score
from utils.input_validation import validate_user_query

logger = logging.getLogger(__name__)
router = APIRouter()

class SearchRequest(BaseModel):
    query: constr(min_length=1, max_length=500)  # M-4: Prevent abuse via long queries
    use_deep_analysis: Optional[bool] = False
    country_code: Optional[str] = "ES"
    forced_intent: Optional[MovieSearchIntent] = None

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
        final_score = normalize_similarity_score(r["score"])
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
@limiter.limit("10/minute")
async def natural_language_search(
    request: Request, # Request object is required for slowapi
    search_req: SearchRequest,
    # Optional auth: magic box is a pre-login guest feature (handoff pre-login
    # splash). current_user is only used to exclude watched films — guests
    # simply skip that filter. Rate limit above applies either way.
    current_user: Optional[TokenResponse] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding_service: EmbeddingService = Depends(get_embedding_service)
):
    """
    Advanced natural language search with semantic expansion and vibe filtering.
    Handles complex queries like "old gangster movie", "90s hidden gem", "short anime".
    Also handles "Movies like X" by detecting title matches.

    Auth required: this endpoint fans out to Groq (GPT-OSS-120B parser +
    optional Deep Analysis). Leaving it open to guests turns it into a
    paid-LLM proxy. The /onboarding/search endpoint covers the public-DB
    title search use case for guests.
    """
    try:
        # Validate input for LLM injection
        search_req.query = validate_user_query(search_req.query)

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

        # 1. Parse Intent with Advanced LLM (or use forced_intent to bypass LLM parsing)
        if search_req.forced_intent:
            intent = search_req.forced_intent
        else:
            try:
                intent = await parse_user_intent(search_req.query)
            except Exception as e:
                logger.warning(f"Groq intent parsing failed, falling back to pure vector search: {e}")
                intent = MovieSearchIntent(
                    semantic_query=search_req.query,
                    reasoning="Groq unavailable — direct vector search",
                )
        logger.info(f"Parsed intent: {intent}")
        logger.info(f"Reasoning: {intent.reasoning}")
        
        # Check for Reference Movie (e.g. "movies like Inception")
        if intent.reference_movie:
            logger.info(f"Detected reference movie in intent: {intent.reference_movie}")
            # Local DB search: substring match against title OR original_title.
            # Substring (with %…%) lets "deprisa deprisa" match "Deprisa, deprisa"
            # and original_title catches Spanish/foreign titles localised in `title`.
            ref_pattern = f"%{intent.reference_movie}%"
            ref_movie_match = await db.execute(
                select(Movie).where(
                    or_(
                        Movie.title.ilike(ref_pattern),
                        Movie.original_title.ilike(ref_pattern),
                    )
                )
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
        loop = asyncio.get_running_loop()
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
        
        # Runtime constraints
        if intent.min_runtime_minutes:
            qdrant_filters["min_runtime"] = intent.min_runtime_minutes
        if intent.max_runtime_minutes:
            qdrant_filters["max_runtime"] = intent.max_runtime_minutes

        # Rating floor
        if intent.min_rating:
            qdrant_filters["min_rating"] = intent.min_rating
        
        # Popularity vibe (hidden_gem, blockbuster, any)
        if intent.popularity_vibe == "blockbuster":
            qdrant_filters["min_vote_count"] = 3000
        elif intent.popularity_vibe == "hidden_gem":
            qdrant_filters["max_vote_count"] = 1000
            
        # Language filter
        if intent.original_language:
            qdrant_filters["original_language"] = intent.original_language

        # NEW (Sprint 1, migration o3p4q5r6s7t8): extended metadata filters.
        # These payload fields aren't currently indexed in Qdrant — we pre-fetch
        # the candidate set from Qdrant by the cheap filters, then DB-filter
        # by the new dimensions before returning. Adding payload indexes is a
        # follow-up (cheap once we know which dimensions get used in anger).
        if intent.mpaa_ratings:
            qdrant_filters["mpaa_ratings"] = intent.mpaa_ratings
        if intent.min_oscar_wins:
            qdrant_filters["min_oscar_wins"] = intent.min_oscar_wins
        if intent.min_imdb_rating is not None:
            qdrant_filters["min_imdb_rating"] = intent.min_imdb_rating
        if intent.min_metacritic is not None:
            qdrant_filters["min_metacritic"] = intent.min_metacritic
        if intent.safe_mode:
            # Default. Exclude TMDB 'adult' titles unless the user explicitly
            # asks for them via the LLM-parsed safe_mode=False.
            qdrant_filters["exclude_adult"] = True

        # 3.5. Exclude Watched Movies (signed-in users only — guests have none)
        watched_tmdb_ids = []
        if current_user is not None:
            result = await db.execute(
                select(Movie.tmdb_id)
                .join(UserRating, Movie.id == UserRating.movie_id)
                .where(UserRating.user_id == current_user.user_id)
                .where(or_(
                    UserRating.rating.isnot(None),
                    UserRating.is_liked.is_(True),
                    UserRating.is_watched.is_(True),
                ))
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

        # Minimum quality gate — drop movies with no TMDB signal (e.g. vote_count=0)
        raw_results = [
            r for r in raw_results
            if (r.get("metadata", {}).get("vote_count") or 0) >= 10
            and (r.get("metadata", {}).get("vote_average") or 0) >= 4.0
        ]
        logger.info(f"After quality gate: {len(raw_results)} results")

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

        # Sprint 1+2 post-filter (DB-side, since these columns aren't in the
        # Qdrant payload yet — migration o3p4q5r6s7t8). See
        # services.magic_search_ranking.movie_passes_post_filter for the
        # per-row decision matrix.
        post_filter_drop = {
            tid for tid, m in db_movies.items()
            if not movie_passes_post_filter(m, intent)
        }
        if post_filter_drop:
            raw_results = [
                r for r in raw_results
                if int(r.get("metadata", {}).get("tmdb_id") or r["movie_id"]) not in post_filter_drop
            ]
            logger.info(
                f"After Magic-Search post-filter: {len(raw_results)} results "
                f"(dropped {len(post_filter_drop)})"
            )

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

            # Compound score: cosine → optional title boost → VBS sigmoid gate.
            # See services.magic_search_ranking.compute_blended_score for the
            # full decision tree + thresholds. Pulled out so the pipeline is
            # testable without the FastAPI / Qdrant / DB stack.
            final_score, title_sim, _quality_weight = compute_blended_score(
                raw_cosine=r["score"],
                query=search_req.query,
                intent=intent,
                title=metadata.get("title") or "",
                vbs=(db_movie.vectorbox_score if db_movie else None),
            )
            if title_sim is not None and title_sim >= 0.85:
                logger.info(
                    f"Title-match boost for {metadata.get('title')} "
                    f"(sim={title_sim:.2f}): {final_score:.1f}"
                )

            result = {
                "movie_id": tmdb_id,
                "title": metadata.get("title", "Unknown"),
                "overview": metadata.get("overview", ""),
                "poster_path": poster_path,
                "score": round(final_score, 0),
                "_final_score": final_score,  # precise float kept for sorting
                "year": metadata.get("year"),
                "runtime": metadata.get("runtime"),
                "genres": metadata.get("genres", []),
                "vote_average": metadata.get("vote_average"),
                # Phase 12 Fields (from DB)
                "vectorbox_score": db_movie.vectorbox_score if db_movie else None,
                "imdb_rating": db_movie.imdb_rating if db_movie else None,
                "metacritic_rating": db_movie.metacritic_rating if db_movie else None,

                "title_es": db_movie.title_es if db_movie else None,
                "overview_es": db_movie.overview_es if db_movie else None
            }
            results.append(result)

        # Sprint 3 (2026-05-15): re-sort by the BLENDED final_score so that
        # title-match boost and the VBS sigmoid gate actually affect ordering.
        # Before this, results came back in raw Qdrant cosine order — the
        # `score` field on each row was the blended value but the FRONTEND
        # only got to see the ordering the API returned. Now Qdrant is the
        # initial filter / coarse rank, and our compound score is the final
        # order. Strip the internal `_final_score` key before returning.
        results.sort(key=lambda r: r.get("_final_score", 0.0), reverse=True)
        for r in results:
            r.pop("_final_score", None)

        # 6. Fetch Streaming Providers
        try:
            provider_service = ProviderService(db, tmdb)
            
            # Get IDs
            result_ids = [r["movie_id"] for r in results if r["movie_id"]]
            
            # Fetch batch (use country_code from request, fallback to ES)
            providers_map = await provider_service.get_providers_batch(
                result_ids, search_req.country_code or "ES"
            )
            
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

        # Deep Analysis (Tier 2 LLM re-rank) — auto-trigger on complexity ≥ 3
        # via services.magic_search_ranking.should_run_deep_analysis. Explicit
        # `use_deep_analysis=True` still wins as an override.
        if should_run_deep_analysis(intent, user_requested=search_req.use_deep_analysis) and results:
            logger.info(
                f"Deep Analysis triggered (explicit={search_req.use_deep_analysis} "
                f"complexity={intent_complexity(intent)}). Calling Tier 2..."
            )
            try:
                # Pass results to GPT-OSS-120B (deep-analysis rerank)
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


@router.get("/showcase")
@limiter.limit("60/minute")
async def showcase_search(
    request: Request,
    slug: str,
    lang: str = "es",
    redis=Depends(get_redis),
):
    """The landing's canned queries. Reads Redis and nothing else.

    This is the counterweight to `/natural` being open: the landing's default
    traffic lands here, where the set of possible inputs is closed (the slugs in
    `showcase_service.SHOWCASE_QUERIES`) and no free text ever reaches Groq.

    A miss returns 503 rather than computing on demand — on purpose. The moment
    this endpoint can trigger a search, the closed-input guarantee is gone and
    it becomes `/natural` with extra steps. Filling the cache is the job of
    `scripts/warm_showcase.py`, run on deploy.
    """
    if not showcase_service.is_valid_slug(slug):
        # 404 before any I/O: an unknown slug costs a dict lookup.
        raise HTTPException(status_code=404, detail="Unknown showcase slug")

    if redis is None:
        raise HTTPException(status_code=503, detail="Showcase cache unavailable")

    payload = await showcase_service.read(redis, slug, lang)
    if payload is None:
        # Cold cache. The landing has a state for this; do not paper over it by
        # running a query, which is exactly what this endpoint exists to avoid.
        logger.warning("Showcase cache miss for slug=%s lang=%s — run warm_showcase.py", slug, lang)
        raise HTTPException(status_code=503, detail="Showcase not warmed yet")

    return payload


@router.get("/autocomplete")
@limiter.limit("60/minute")
async def autocomplete_search(
    request: Request,
    q: str,
    tmdb: TMDBClient = Depends(get_tmdb_client)
):
    """
    Fast title autocomplete backing the More Like This search.
    Searches TMDB directly to support multiple languages and broad coverage.
    """
    if len(q.strip()) < 2:
        return []

    data = await tmdb._make_request("/search/movie", {"query": q.strip(), "include_adult": "false"})
    if not data or not data.get("results"):
        return []

    results = []
    for m in data["results"][:8]:
        results.append({
            "tmdb_id": m["id"],
            "title": m["title"],
            "year": int(m["release_date"][:4]) if m.get("release_date") else None,
            "poster_path": m.get("poster_path"),
            "overview": m.get("overview", "")
        })
    return results

