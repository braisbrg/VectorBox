from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, constr
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import asyncio
from config import get_db
from dependencies import get_tmdb_client, get_qdrant_service, get_embedding_service, get_current_user, get_optional_current_user, get_redis
from models.schemas import TokenResponse
from services.nlp_search import parse_user_intent, parse_failed, finalize_intent, search_with_reasoning, MovieSearchIntent
from services.magic_search_ranking import (
    CONFIDENCE_SAMPLE,
    LOW_CONFIDENCE_MEAN,
    OPEN_REQUEST_MIN_VBS,
    compute_blended_score,
    has_descriptive_filters,
    intent_complexity,
    is_low_confidence,
    is_quality_only_request,
    SEARCH_RESULT_LIMIT,
    search_fetch_limit,
    movie_passes_post_filter,
    search_confidence,
    should_run_deep_analysis,
    title_sim_score,
)
from services import showcase_service
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService
from services.tmdb_client import TMDBClient
from services.provider_service import ProviderService
from models.database import UserRating, Movie
from sqlalchemy import func, select, or_
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
    # True when the catalogue had nothing close enough to be a recommendation.
    # `results` is empty in that case — deliberately: showing the twenty nearest
    # films under a "we are not sure" banner is worse than showing none, because
    # the engine looks confident about films it picked for no reason.
    low_confidence: bool = False
    # True when no model parsed the sentence, so the answer came from the raw
    # text and not from an understanding of it. The results are still real films;
    # what is missing is every constraint the user expressed. The UI owes them
    # that fact — silently serving a worse answer is the one option we ruled out.
    degraded: bool = False

def filter_es_providers(all_providers: List[str]) -> List[str]:
    """Pure function to filter provider names against the ES whitelist."""
    es_whitelist = {"Netflix", "Amazon Prime Video", "HBO Max", "Disney+", "Apple TV", "Movistar+", "Filmin"}
    return [p for p in all_providers if p in es_whitelist]


async def _item_to_item_search(
    movie_id: int,
    movie_title: str,
    qdrant: QdrantService,
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

# ── Fase 3 of the landing plan (2026-07-28) ─────────────────────────────────
#
# This handler used to BE the public endpoint, while its own docstring said the
# opposite: "Auth required: this endpoint fans out to Groq… Leaving it open to
# guests turns it into a paid-LLM proxy." Someone reasoned that through and the
# code drifted from it. Free text plus no session plus a 200k-token daily budget
# is a free LLM proxy for anyone who finds the URL, and on 2026-07-25 the budget
# was in fact exhausted (197,753 of 200,000 used).
#
# The body is now shared by two doors with different trust:
#   POST /natural  — signed in. Full budget: 500-char queries, Tier-2 deep
#                    analysis, 10/minute.
#   POST /try      — anonymous. Deliberately bounded: 140 chars, no Tier-2, and
#                    5/minute. Enough to try the product, too little to farm.
#
# The landing does not use either by default: its chips read /search/showcase,
# which is a cache with a closed input set. This path only runs when a visitor
# types something of their own.
#
# Set on the response when the answer came from the catalogue rather than from
# the vector. A constant because scripts/audit_search.py asserts which branch
# answered, and matching on a prose sentence is a test that breaks on a typo.
CATALOGUE_SELECTION_REASONING = "A varied selection of well-regarded films from the catalogue."
AUDIENCE_SELECTION_REASONING = "Films chosen for who is watching, ranked by the catalogue's own score."

CATALOGUE_SELECTION_SIZE = 12
CATALOGUE_SELECTION_POOL = 40


async def _catalogue_selection(db: AsyncSession, floor: float, genres: Optional[List[str]] = None):
    """Films straight from the catalogue: a quality bar, and nothing else.

    Shuffled rather than ordered by score, so the same question twice does not
    return the same twelve films. The bar is what makes it a good answer; the
    order within it is not information.
    """
    q = (
        select(Movie)
        .where(Movie.vectorbox_score >= floor)
        .where(Movie.poster_path.is_not(None))
    )
    if genres:
        q = q.where(Movie.genres.overlap(genres))
    picks = (await db.execute(
        q.order_by(func.random()).limit(CATALOGUE_SELECTION_POOL)
    )).scalars().all()

    if genres:
        # The genre IS the coherence the user asked for. Spreading across lead
        # genres here — which is right when there is no filter — would undo it.
        return picks[:CATALOGUE_SELECTION_SIZE]

    seen_genres: set[str] = set()
    varied: list[Movie] = []
    for m in picks:
        lead = (m.genres or ["?"])[0]
        if lead in seen_genres and len(varied) < CATALOGUE_SELECTION_SIZE:
            continue
        seen_genres.add(lead)
        varied.append(m)
        if len(varied) >= CATALOGUE_SELECTION_SIZE:
            break
    return varied


def _catalogue_results(movies) -> List[dict]:
    return [{
        "movie_id": m.tmdb_id, "title": m.title, "overview": m.overview,
        "poster_path": m.poster_path, "score": round(m.vectorbox_score or 0),
        "year": m.year, "runtime": m.runtime, "genres": m.genres or [],
        "vote_average": m.vote_average, "vectorbox_score": m.vectorbox_score,
        "title_es": m.title_es, "overview_es": m.overview_es,
    } for m in movies]


async def _run_natural_search(
    search_req: SearchRequest,
    current_user: Optional[TokenResponse],
    db: AsyncSession,
    tmdb: TMDBClient,
    qdrant: QdrantService,
    embedding_service: EmbeddingService,
):
    """Shared body. Advanced natural-language search with semantic expansion.

    Also handles "Movies like X" by detecting title matches and switching to
    item-to-item, which short-circuits before the LLM — that path costs nothing.

    Not a route: the two routes below decide who may reach it and with what
    budget. Anything trusted must be enforced by the CALLER, not here.
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
                potential_movie_id, potential_movie_title, qdrant
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
                # Same wording parse_user_intent uses for its own give-ups, so
                # one predicate (parse_failed) covers every route into this state.
                intent = finalize_intent(MovieSearchIntent(
                    semantic_query=search_req.query,
                    reasoning=f"LLM unavailable: {e}",
                ), search_req.query)
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
                    potential_movie_id, potential_movie_title, qdrant
                )
                if result:
                    return result

        # Audience requests never reach the vector, and that is the point.
        #
        # Measured 2026-07-29: "family friendly, gentle, wholesome, safe for all
        # ages" scores 0.548 over its top ten neighbours — HIGHER than "the
        # loneliness of living in a huge city" at 0.505, one of the queries this
        # engine answers best. So no confidence threshold can ever catch it: the
        # vector is not weakly right, it is confidently wrong. The catalogue is
        # embedded on what a film is ABOUT, so "familiar" finds cinema ABOUT
        # families — Uncle Buck, Charlotte's Web, at a mean VBS of 55.
        #
        # The metadata already holds the right answer. Genre plus the catalogue's
        # own score gives Spirited Away (99), WALL·E (97), Toy Story (97).
        #
        # Placed after the reference-movie branch so "peliculas como Origen"
        # still wins, and before the embedding so this path costs neither the
        # CPU-bound encode nor a Qdrant round trip.
        #
        # mpaa_ratings is deliberately NOT applied: it covers 74.9% of the
        # catalogue, so requiring it would drop a quarter of the films for having
        # no certification rather than for being unsuitable.
        # A degraded run must not be reported as an unanswerable question.
        # Measured with scripts/audit_search.py: Groq's free tier caps at 8000
        # tokens per MINUTE, a parse costs ~2000, and four searches in a row
        # exhaust it. With no parse there is no `open_request` and no
        # `min_vectorbox_score`, so every gentle query — "no se que ver", "para
        # llorar esta noche" — fell straight through to the refusal and the user
        # got an empty page. The catalogue branch needs no LLM at all, so a
        # degraded run answers from it instead of apologising.
        degraded = parse_failed(intent)

        # Genres are required, not optional. Without them this branch selects on
        # nothing but the quality bar and hands back whatever the catalogue's top
        # scorers happen to be — measured, "a movie parents and kids will both
        # enjoy" returned Athlete A and The Spirit of the Beehive at VBS 85. That
        # is the failure this branch exists to fix, wearing a better score. When
        # the cue list is what fired, ensure_audience_request supplies them; when
        # only the model flagged it and named no genre, the vector path is the
        # honest fallback (it answered that same query with My Big Fat Greek
        # Wedding at VBS 55 — worse on paper, right in kind).
        if intent.audience_request and intent.include_genres:
            logger.info("Audience request %r (genres=%s)", search_req.query, intent.include_genres)
            picks = await _catalogue_selection(
                db, OPEN_REQUEST_MIN_VBS, intent.include_genres
            )
            return SearchResponse(
                results=_catalogue_results(picks),
                intent={**intent.model_dump(), "reasoning": AUDIENCE_SELECTION_REASONING},
                degraded=degraded,
            )

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
        # Payload-backed since 2026-07-29. Before that these were enforced only
        # in Postgres, AFTER the search — so they subtracted from twenty
        # neighbours instead of narrowing the search. min_vectorbox_score was
        # never passed here at all, though Qdrant has supported it all along.
        if intent.countries:
            qdrant_filters["countries"] = intent.countries
        if intent.spoken_languages:
            qdrant_filters["spoken_languages"] = intent.spoken_languages
        if intent.min_vectorbox_score is not None:
            qdrant_filters["min_vectorbox_score"] = intent.min_vectorbox_score
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
        # Wider when a Postgres-side post-filter has to survive the fetch — see
        # services.magic_search_ranking.search_fetch_limit for the measurement.
        raw_results = await qdrant.search_similar(
            query_vector=query_vector,
            limit=search_fetch_limit(intent),
            score_threshold=0.3, # Semantic search standard
            filters=qdrant_filters
        )
        
        logger.info(f"Qdrant returned {len(raw_results)} results")

        # Confidence gate. Measured over 54 runs of an 18-query panel
        # (scripts/experiment_confidence.py): answerable questions never fell
        # below a 0.443 mean over the top ten neighbours, unanswerable ones never
        # rose above 0.425. Below the threshold the catalogue has nothing close
        # enough to call a recommendation, and returning the nearest twenty
        # anyway is how "receta de tortilla de patatas" used to answer with
        # Ratatouille — confidently, and wrong.
        #
        # Read BEFORE the quality gate and the post-filter: those drop films for
        # reasons unrelated to whether the question made sense.
        cosines = [r.get("score") or 0.0 for r in raw_results]
        confidence = search_confidence(cosines)


        # A weak vector is not the same as an unanswerable question. Measured
        # 2026-07-29, three different things were scoring below the threshold:
        #
        #   "algo muy aclamado por la critica"  0.355  min_metacritic=75
        #   "algo corto, menos de 90 minutos"   0.371  max_runtime_minutes=90
        #   "no se que ver"                     0.306  no filters
        #   "receta de tortilla de patatas"     0.232  no filters
        #
        # The first two are perfectly answerable — just by FILTERS rather than by
        # similarity — and refusing them was a bug. The third is a real request
        # for a good default that nobody can make more specific: telling someone
        # who does not know what to watch to be more precise leaves them with
        # nothing. Only the fourth is genuinely unanswerable.
        #
        # Confidence cannot separate the third from the fourth (0.306 vs 0.232 is
        # inside the noise), so the parser flags it as `open_request`.
        if (is_low_confidence(cosines) and not has_descriptive_filters(intent)
                and not intent.open_request and not intent.audience_request
                and not degraded):
            logger.info(
                "Low-confidence query (mean top-%d cosine %.3f < %.2f): %r",
                CONFIDENCE_SAMPLE, confidence, LOW_CONFIDENCE_MEAN, search_req.query,
            )
            return SearchResponse(
                results=[],
                intent={**intent.model_dump(), "confidence": round(confidence, 3)},
                low_confidence=True,
                degraded=degraded,
            )

        # "I don't know what to watch". The vector is meaningless here — it was
        # returning Glitter (VBS 14) for "sorprendeme con algo bueno" — so the
        # answer comes from the catalogue's own quality, spread across genres so
        # it reads as a selection rather than a leaderboard.
        # When the vector says nothing but the request still has criteria, the
        # answer must come from the CATALOGUE, not from twenty arbitrary
        # neighbours. Three shapes end up here:
        #
        #   "no se que ver"                    -> open_request, no criteria
        #   "peliculas muy bien valoradas"     -> a quality bar and nothing else
        #   parser down (rate limit)           -> no criteria we can read
        #
        # All three used to return zero. The first was refused outright; the
        # second passed the gate and then found almost nothing, because the
        # twenty nearest neighbours of a meaningless vector rarely clear a
        # quality bar. Querying the catalogue directly is the honest answer.
        # audience_request lands here only when it named no genre — with one it
        # was answered before the embedding. Someone describing the room is still
        # asking for a suggestion, so refusing them is the failure with no
        # recovery. Verified: "algo que terminemos mis padres y yo sin discutir"
        # was returning an empty page.
        if is_low_confidence(cosines) and (
            intent.open_request or intent.audience_request
            or is_quality_only_request(intent) or degraded
        ):
            floor = intent.min_vectorbox_score or OPEN_REQUEST_MIN_VBS
            logger.info("Catalogue selection for %r (floor=%s, open=%s, degraded=%s)",
                        search_req.query, floor, intent.open_request, degraded)
            picks = await _catalogue_selection(db, floor)
            return SearchResponse(
                results=_catalogue_results(picks),
                intent={**intent.model_dump(), "confidence": round(confidence, 3),
                        "reasoning": CATALOGUE_SELECTION_REASONING},
                degraded=degraded,
            )

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
        # Truncate BEFORE the provider fan-out below: a post-filtered query now
        # fetches up to 150 candidates, and every survivor would otherwise cost a
        # provider lookup and a row in the response.
        del results[SEARCH_RESULT_LIMIT:]
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
            intent=intent.model_dump(),
            degraded=degraded,
        )
        
    except HTTPException:
        # validate_user_query raises 400 on a prompt-injection attempt, and the
        # blanket handler below was turning that into "Search service
        # unavailable" — the guard worked and then reported itself as our
        # outage. Any deliberate status set upstream travels unchanged.
        raise
    except Exception as e:
        import traceback
        logger.error(f"Search failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Search service unavailable")


@router.post("/natural", response_model=SearchResponse)
@limiter.limit("10/minute")
async def natural_language_search(
    request: Request,  # required by slowapi
    search_req: SearchRequest,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
):
    """Magic Box for signed-in users. Full budget.

    Auth is required again as of Fase 3 — the docstring said so all along while
    the signature said `get_optional_current_user`. Guests get `/try` below.
    """
    return await _run_natural_search(
        search_req, current_user, db, tmdb, qdrant, embedding_service
    )


# A guest sentence is a sentence, not an essay: 140 characters fits every example
# query the landing ships and every phrasing we tested, while making the endpoint
# useless as a general-purpose LLM proxy.
TRY_MAX_QUERY_LENGTH = 140


class TrySearchRequest(BaseModel):
    # extra="forbid" so a caller who tries to smuggle `forced_intent` or
    # `use_deep_analysis` gets a 422 instead of a silent 200. Pydantic would drop
    # them either way, but a contract that answers "no" is worth more than one
    # that quietly ignores you — and it makes the attempt visible in the logs.
    model_config = ConfigDict(extra="forbid")

    query: constr(min_length=1, max_length=TRY_MAX_QUERY_LENGTH)
    country_code: Optional[str] = "ES"
    # Deliberately absent: `use_deep_analysis` (Tier-2 is the expensive LLM call)
    # and `forced_intent` (an internal bypass — accepting it from the public
    # would let a caller hand-craft filters and skip every guard we have).


@router.post("/try", response_model=SearchResponse)
@limiter.limit("5/minute")
async def try_search(
    request: Request,  # required by slowapi
    try_req: TrySearchRequest,
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
):
    """The public door: type your own sentence without an account.

    Bounded on every axis that costs money — 140 characters, 5/minute, no Tier-2
    deep analysis, no forced_intent. A visitor can try the product; nobody can
    farm the daily Groq budget through it.

    `current_user=None` is passed explicitly rather than resolved: this route
    must behave identically for everyone, and reading a session here would make
    a signed-in user's results differ from a guest's on the same URL.
    """
    return await _run_natural_search(
        SearchRequest(query=try_req.query, country_code=try_req.country_code,
                      use_deep_analysis=False, forced_intent=None),
        None, db, tmdb, qdrant, embedding_service,
    )


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

