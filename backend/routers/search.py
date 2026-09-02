from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, constr
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import asyncio
import random
import re
import unicodedata
from config import get_db
from dependencies import get_tmdb_client, get_qdrant_service, get_embedding_service, get_current_user, get_optional_current_user, get_redis
from models.schemas import TokenResponse
from services.nlp_search import parse_user_intent, parse_user_intent_cached, parse_failed, finalize_intent, search_with_reasoning, MovieSearchIntent, detectar_watchlist
from services.magic_search_ranking import (
    CONFIDENCE_SAMPLE,
    LOW_CONFIDENCE_MEAN,
    OPEN_REQUEST_MIN_VBS,
    RELAXED_MIN_ROW,
    relaxable_dimension,
    relaxed_filters,
    compute_relevance,
    has_descriptive_filters,
    intent_complexity,
    is_low_confidence,
    is_quality_only_request,
    SEARCH_RESULT_LIMIT,
    search_fetch_limit,
    movie_passes_post_filter,
    search_confidence,
    should_run_deep_analysis,
    trim_to_relevant,
    title_sim_score,
)
from services import lexical_channel
from services import bm25
from services import metrics
from services.lexical_channel import load_vocabulary, terms_in
from services import showcase_service
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService
from services.tmdb_client import TMDBClient
from services.provider_service import ProviderService
from models.database import UserRating, Movie
from sqlalchemy import case, func, nulls_last, select, or_, text
from utils.scoring import normalize_film_similarity_score, normalize_similarity_score
from utils.input_validation import validate_user_query

logger = logging.getLogger(__name__)
router = APIRouter()

class SearchRequest(BaseModel):
    query: constr(min_length=1, max_length=500)  # M-4: Prevent abuse via long queries
    use_deep_analysis: Optional[bool] = False
    country_code: Optional[str] = "ES"
    forced_intent: Optional[MovieSearchIntent] = None
    # Conmutador explícito. Se SUMA a la detección de frase: pedirlo por escrito y
    # pedirlo con el botón son la misma intención, y basta con una de las dos.
    watchlist: Optional[bool] = False

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
    # Which filter was dropped to fill a short row ("era" / "countries"), or None.
    # The rows it produced carry `outside_filters` with the same value: they
    # answer the SUBJECT but not the whole request, and the UI has to say so.
    # Appending them silently would be a worse lie than the padding this replaces.
    relaxed_filter: Optional[str] = None
    # True cuando la búsqueda se acotó a la watchlist. Lo necesita la UI porque el
    # ámbito puede venir de la FRASE y no del botón: sin esto el conmutador se
    # quedaría apagado mientras los resultados sí están filtrados, y el usuario
    # leería como catálogo entero algo que es su lista.
    watchlist_applied: bool = False

def filter_es_providers(all_providers: List[str]) -> List[str]:
    """Pure function to filter provider names against the ES whitelist."""
    es_whitelist = {"Netflix", "Amazon Prime Video", "HBO Max", "Disney+", "Apple TV", "Movistar+", "Filmin"}
    return [p for p in all_providers if p in es_whitelist]


async def _item_to_item_search(
    movie_id: int,
    movie_title: str,
    qdrant: QdrantService,
    watchlist_ids: Optional[List[int]] = None,
) -> Optional[SearchResponse]:
    """Shared helper for Item-to-Item recommendation (deduplicated)."""
    vector = await qdrant.get_vector(movie_id)
    if not vector:
        return None
    raw_results = await qdrant.search_similar(
        query_vector=vector,
        limit=20,
        score_threshold=0.4,
        # "como Memento, de mi lista" entra por aquí: este camino corta antes del
        # parser, así que si el ámbito no se aplicase también aquí la frase se
        # ignoraría en silencio justo en las consultas más concretas.
        filters={"exclude_tmdb_ids": [movie_id],
                 **({"include_tmdb_ids": watchlist_ids} if watchlist_ids else {})}
    )
    results = []
    for r in raw_results:
        metadata = r.get("metadata", {})
        # Escala película→película: aquí se compara el vector GUARDADO de una
        # película contra el catálogo, no un texto embebido. Con la escala de
        # consultas, 15 de los 20 vecinos de The Matrix salían 99 — y Blade Runner
        # y Come and See, 20 de 20. Ver utils/scoring.py para las dos poblaciones.
        final_score = normalize_film_similarity_score(r["score"])
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
        },
        watchlist_applied=bool(watchlist_ids),
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


async def _catalogue_selection(
    db: AsyncSession,
    floor: float,
    genres: Optional[List[str]] = None,
    top_ranked: bool = False,
):
    """Films straight from the catalogue: a quality bar, and nothing else.

    Two shapes of question end up here and they want opposite orderings.

    "no se que ver" wants VARIETY — the bar is what makes the answer good, the
    order within it is not information, and returning the same twelve films every
    time would be a worse answer to the same question. So: sample above the floor.

    "las mejores peliculas de la historia" wants the TOP. Sampling above a floor
    answered it, on 2026-07-30, with Harry Potter and the Deathly Hallows Part 1,
    A Quiet Place Part II and How to Train Your Dragon 3 — respectable films, and
    a wrong answer to a superlative. So: rank first, then sample the head, which
    keeps some rotation without pretending a random 78 belongs on that list.
    """
    q = (
        select(Movie)
        .where(Movie.vectorbox_score >= floor)
        .where(Movie.poster_path.is_not(None))
    )
    if genres:
        q = q.where(Movie.genres.overlap(genres))
    q = q.order_by(Movie.vectorbox_score.desc().nulls_last()) if top_ranked else q.order_by(func.random())
    picks = (await db.execute(q.limit(CATALOGUE_SELECTION_POOL))).scalars().all()
    if top_ranked:
        # The head is already the answer; shuffling inside it only decides which
        # of the catalogue's very best show up today.
        picks = random.sample(picks, min(len(picks), CATALOGUE_SELECTION_POOL))

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
        "poster_path": m.poster_path,
        # No query vector reached this branch, so there is no distance to report.
        # It used to send `vectorbox_score`, which the UI printed as closeness to
        # the search: "no se que ver" came back at 99 while a precise, correctly
        # answered query showed 83.
        "score": None,
        "year": m.year, "runtime": m.runtime, "genres": m.genres or [],
        "vote_average": m.vote_average, "vectorbox_score": m.vectorbox_score,
        "title_es": m.title_es, "overview_es": m.overview_es,
    } for m in movies]


def _normalize_needle(s: str) -> str:
    """Canonical form of a title for matching: no accents, no punctuation.

    Mirrors `_match_expression` below in Python so both sides of the comparison
    are folded the same way. NFKD splits "á" into "a" + combining accent and the
    Mn filter drops the accent, which is what `unaccent()` does server-side.
    """
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", s or "")
        if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


def _match_expression(column):
    """The same folding as `_normalize_needle`, server-side.

    No functional index behind it: `unaccent()` is STABLE, not IMMUTABLE, so it
    cannot be indexed without a wrapper, and this runs once per search. Measured
    on the 20k-row catalogue before shipping — see the integration panel.
    """
    return func.trim(func.regexp_replace(
        func.unaccent(func.lower(column)), r"[^a-z0-9]+", " ", "g"
    ))


async def _pick_reference_movie(db: AsyncSession, needle: str, *, substring: bool):
    """The BEST film matching `needle`, never an arbitrary one.

    Both title lookups used `.first()` on an unordered SELECT, which returns
    whatever Postgres happens to yield — physical order, in practice. 811
    catalogue titles are shared by two or more films (measured 2026-07-31), so
    that was a coin flip on every one of them:

        "Mother"    Bong Joon-ho (VBS 86) / 1926 Pudovkin (65) / 2019 (65)
        "The Hunt"  Vinterberg (91) / Blumhouse 2020 (54)
        "Dracula"   Lugosi 1931 (74) / 2025 (51)

    `collection_name` joins the substring search because a FRANCHISE is the
    commonest thing a person names when they mean "something in this vein", and
    it was the one column not being read. "james bond" matches no title in the
    catalogue — the 27 Bond films are called Dr. No, Goldfinger, Skyfall — so the
    only title hits were `Being James Bond` (a Daniel Craig documentary) and
    `Untitled James Bond Film`, an unreleased placeholder with no year and no
    score. That is what "Movies like Being James Bond" came from.

    Ordering: an exact title beats a substring, then the catalogue's own score.
    `nulls_last` is what keeps the `Untitled …` placeholder rows — 5+ of them
    carry `is_upcoming=False`, so the release filter does not catch them — from
    ever winning on a NULL score.

    `title_es` joins them because the product is Spanish-first and it was the
    other column nobody read: "el padrino" found nothing at all, while the row
    for The Godfather carries `title_es='El padrino'`.

    Matching is accent- and punctuation-insensitive on both sides (see
    `_normalize_needle`), which is what finally makes "deprisa deprisa" reach
    `original_title='Deprisa, deprisa'` and "la naranja mecanica" reach
    `title_es='La naranja mecánica'`. The docstring this replaces claimed the
    comma case already worked; it never did.
    """
    hit = await _lookup(db, needle, substring=substring, folded=False)
    if hit is not None:
        return hit
    # Only now pay for the fold. Measured on the 20k-row catalogue: plain ILIKE
    # 52 ms, folded 101 ms (the fold roughly doubles it), and this runs on every
    # search — so the second pass is reserved for the queries the first one
    # MISSES, which today return nothing at all. Both are sequential scans; a
    # pg_trgm GIN index would fix the 52 ms too, and is not worth a migration
    # while an LLM parse on the same request costs 1-2 s.
    #
    # ponytail: a folded-only match that scores higher than a plain hit loses,
    # because the plain pass returns first. Merge the two passes only if a real
    # query is ever shown to pick the wrong film because of it.
    return await _lookup(db, needle, substring=substring, folded=True)


# Umbral MEDIDO, no elegido (2026-08-10, 12 consultas sobre el catálogo de 20k):
# el acierto más flojo es "parasyte" → Parasite a 0.500, y el mejor falso positivo
# es una consulta sin sentido a 0.370. 0.45 cae entre las dos poblaciones. Subirlo
# pierde las erratas gordas; bajarlo empieza a contestar a cualquier cosa, que es
# peor que no contestar: una sugerencia inventada manda al usuario a otra película.
FUZZY_MIN_SIMILARITY = 0.45


async def _fuzzy_title_rows(db: AsyncSession, needle: str, limit: int = 8):
    """Títulos del catálogo por parecido de trigramas, con la FORMA de una fila TMDB.

    Devuelve dicts con las mismas claves que `/search/movie` para que el resto del
    autocompletado no tenga que saber de dónde vino cada fila. `year` se sirve como
    `release_date` por eso mismo — la alternativa era un segundo camino paralelo en
    el armado de la respuesta, y dos caminos es como se pierde un campo.

    Se ordena por parecido y luego por VBS: entre varias "Dracula" idénticas a 1.000
    de similitud, la mejor valorada es la que alguien que escribe "dracula" quiere.
    """
    rows = await db.execute(text(
        """
        SELECT tmdb_id, title, year, poster_path, overview,
               GREATEST(
                   similarity(lower(title), :q),
                   similarity(lower(COALESCE(title_es, '')), :q),
                   similarity(lower(COALESCE(original_title, '')), :q)
               ) AS sim
        FROM movies
        WHERE is_excluded IS FALSE AND poster_path IS NOT NULL
        ORDER BY sim DESC, vectorbox_score DESC NULLS LAST
        LIMIT :lim
        """
    ), {"q": needle.lower(), "lim": limit})
    out = []
    for r in rows.mappings():
        if r["sim"] < FUZZY_MIN_SIMILARITY:
            break  # ya vienen ordenadas: la primera por debajo del umbral cierra
        out.append({
            "id": r["tmdb_id"],
            "title": r["title"],
            "release_date": str(r["year"]) if r["year"] else None,
            "poster_path": r["poster_path"],
            "overview": r["overview"] or "",
            "vote_count": 0,
            "popularity": 0,
        })
    if out:
        logger.info("[autocomplete] fuzzy fallback for %r -> %d filas", needle, len(out))
    return out


async def _lookup(db: AsyncSession, needle: str, *, substring: bool, folded: bool):
    """One pass of the title lookup, plain or accent/punctuation-folded."""
    columns = [Movie.title, Movie.original_title, Movie.title_es]
    if substring:
        columns.append(Movie.collection_name)

    if folded:
        target = _normalize_needle(needle)
        if not target:
            return None
        expr = _match_expression
        conditions = [
            expr(c).like(f"%{target}%") if substring else expr(c) == target
            for c in columns
        ]
        exact_clause = expr(Movie.title) == target
    else:
        pattern = f"%{needle}%" if substring else needle
        conditions = [c.ilike(pattern) for c in columns]
        exact_clause = Movie.title.ilike(needle)

    result = await db.execute(
        select(Movie)
        .where(or_(*conditions))
        .order_by(
            case((exact_clause, 0), else_=1),
            nulls_last(Movie.vectorbox_score.desc()),
        )
        .limit(1)
    )
    return result.scalars().first()


async def _run_natural_search(
    search_req: SearchRequest,
    current_user: Optional[TokenResponse],
    db: AsyncSession,
    tmdb: TMDBClient,
    qdrant: QdrantService,
    embedding_service: EmbeddingService,
    redis=None,
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

        # Ámbito watchlist: el botón O la frase, indistintamente. Sólo con sesión —
        # `/try` pasa `current_user=None` a propósito para que la puerta pública
        # responda igual a todo el mundo, y un invitado no tiene lista que acotar.
        watchlist_ids: List[int] = []
        pide_lista = False
        if current_user is not None:
            search_req.query, detectada = detectar_watchlist(search_req.query)
            pide_lista = bool(search_req.watchlist or detectada)
        if pide_lista:
            watchlist_ids = list((await db.execute(
                select(Movie.tmdb_id)
                .join(UserRating, UserRating.movie_id == Movie.id)
                .where(UserRating.user_id == current_user.user_id)
                .where(UserRating.is_watchlist.is_(True))
                .where(UserRating.is_watched.is_(False))
            )).scalars().all())
            # Lista vacía ≠ sin filtro. `search_similar` descarta `include_tmdb_ids`
            # cuando viene vacío (`... and filters[...]`), así que dejarlo pasar
            # devolvería el catálogo ENTERO fingiendo que se filtró. Se corta aquí,
            # que además ahorra la llamada al LLM.
            if not watchlist_ids:
                return SearchResponse(
                    results=[],
                    intent={"semantic_query": search_req.query,
                            "reasoning": "Your watchlist is empty."},
                    watchlist_applied=True,
                )

        # 0. Check if query is a specific movie title (Item-to-Item Search)
        potential_movie_id = None
        potential_movie_title = None
        
        # Search local DB first for exact match
        local_movie = await _pick_reference_movie(db, search_req.query, substring=False)

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
                potential_movie_id, potential_movie_title, qdrant, watchlist_ids
            )
            if result:
                return result

        # 1. Parse Intent with Advanced LLM (or use forced_intent to bypass LLM parsing)
        if search_req.forced_intent:
            intent = search_req.forced_intent
        else:
            try:
                intent = await parse_user_intent_cached(redis, search_req.query)
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
            # Local DB search: substring match against title, original_title or
            # collection. Substring (with %…%) lets "deprisa deprisa" match
            # "Deprisa, deprisa" and original_title catches Spanish/foreign titles
            # localised in `title`. See _pick_reference_movie for the ordering —
            # picking the FIRST of several matches was how "james bond" answered
            # with a documentary.
            ref_movie = await _pick_reference_movie(db, intent.reference_movie, substring=True)

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
                    potential_movie_id, potential_movie_title, qdrant, watchlist_ids
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

        # El único contador de backend de los siete del embudo. Aquí y no en cada
        # `return degraded=...`: todas las ramas de abajo leen ESTA variable, así que
        # un solo sitio los cubre todos y no puede desincronizarse con ninguno.
        # `bump` nunca levanta ni bloquea la respuesta (services/metrics.py).
        if degraded:
            await metrics.bump(redis, "search.degraded")

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
                watchlist_applied=bool(watchlist_ids),
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
        if watchlist_ids:
            qdrant_filters["include_tmdb_ids"] = watchlist_ids
        
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
        # Canal léxico — Fase 2. Antes era un OR de keywords del catálogo fusionado
        # en Python; ahora es un vector sparse BM25 y la fusión RRF la hace Qdrant
        # en el servidor, con el IDF calculado sobre el corpus real. Esa es la
        # pieza que faltaba: el OR daba a `giallo` (6 películas) y a `baroque` (3)
        # el mismo peso, y por eso `baroque` metía a Bach en una consulta de
        # giallo. BM25 pondera por rareza en vez de admitir o no.
        # FASE 2, RESULTADO NEGATIVO — 2026-08-04.
        #
        # El plan decía sustituir este OR de keywords por BM25 sparse con IDF de
        # servidor. Se implementó entero y se midió contra el golden set en tres
        # variantes. Las tres pierden:
        #
        #   sólo denso                             0.826 / 0.689
        #   OR de keywords + fusión cliente (esto) 0.861 / 0.724
        #   BM25 + fusión RRF en servidor          0.796 / 0.681
        #   BM25 + acantilado por canal            0.744 / 0.682
        #   BM25 restringido al vocabulario        0.740 / 0.669
        #
        # El porqué, en una frase: `include_keywords` FILTRA a las películas que
        # llevan literalmente esa keyword, mientras BM25 PUNTÚA todo el texto
        # indexado — sinopsis, reparto, director — así que cualquier película con
        # "crime" en algún sitio entra. Con 20k documentos de prosa corta escrita
        # por un LLM y unas keywords curadas de TMDB, el campo curado ya ES la
        # señal léxica de alta precisión: casarla exacta le gana a puntuar la
        # prosa. El consejo estándar de la industria (híbrido BM25+denso) asume
        # texto largo y heterogéneo, que no es este corpus.
        #
        # El vector sparse se queda en la colección: no estorba, está cubierto por
        # tests, y para la barra de búsqueda (Fase 4) sí es la herramienta
        # correcta — ahí se busca un título o un director exacto, no un tema.
        lexical_terms = (
            terms_in(intent.semantic_query, await load_vocabulary(db))
            if lexical_channel.ENABLED else []
        )

        raw_results = await qdrant.search_similar(
            query_vector=query_vector,
            limit=search_fetch_limit(intent),
            score_threshold=0.3, # Semantic search standard
            filters=qdrant_filters,
        )
        if lexical_terms:
            lexical_hits = await qdrant.search_similar(
                query_vector=query_vector,
                limit=search_fetch_limit(intent),
                score_threshold=0.3,
                filters={**qdrant_filters, "include_keywords": lexical_terms},
            )
            if lexical_hits:
                seen = {r["movie_id"] for r in raw_results}
                for r in lexical_hits:
                    if r["movie_id"] not in seen:
                        r["lexical_match"] = True
                        raw_results.append(r)
                logger.info("Lexical channel %s: +%d candidatos",
                            lexical_terms, len(raw_results) - len(seen))
        
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
                watchlist_applied=bool(watchlist_ids),
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
            # An explicit quality bar is a request for the top, not for a
            # sample of the acceptable. open_request is the opposite.
            picks = await _catalogue_selection(
                db, floor, top_ranked=bool(intent.min_vectorbox_score)
            )
            return SearchResponse(
                results=_catalogue_results(picks),
                intent={**intent.model_dump(), "confidence": round(confidence, 3),
                        "reasoning": CATALOGUE_SELECTION_REASONING},
                degraded=degraded,
                watchlist_applied=bool(watchlist_ids),
            )

        # Relevance cliff — see services.magic_search_ranking.trim_to_relevant.
        # Placed BEFORE the quality gate and the DB fetch so the films it drops
        # cost neither a Postgres row nor a TMDB detail call nor a provider
        # lookup, and AFTER the confidence branches so it never turns a query
        # that was going to be answered from the catalogue into a short vector row.
        # The cliff measures COSINE, and a lexical hit is here because it carries
        # the term — its cosine is low by construction, since the query is phrased
        # in today's words and the film's description is not. Trimming it on
        # cosine undoes the rescue: `I Walked with a Zombie` came back at 0.34
        # against a top of 0.44, so 0.77 of the best fell under the 0.85 floor and
        # the channel's whole contribution vanished. The floor is therefore
        # computed from the vector channel and applied only to it.
        # El acantilado se aplica a CADA CANAL contra su propio mejor, no una vez
        # sobre la mezcla. Los dos motivos, ambos medidos:
        #
        #   · Cortar la mezcla con el mejor coseno borra los rescates léxicos,
        #     que tienen coseno bajo por construcción (0.796/0.681, peor que no
        #     hacer nada).
        #   · Eximir al canal léxico entero es peor todavía: BM25 devuelve veinte
        #     coincidencias débiles para CUALQUIER consulta, y sin recorte entran
        #     las veinte — 0.434 de nDCG y 64 irrelevantes en los top-10.
        #
        # Un canal propone lo que está cerca de su propio mejor. Las escalas no
        # se comparan entre sí en ningún momento, que es de donde venían los dos
        # fallos.
        before_cliff = len(raw_results)
        dense_hits = [r for r in raw_results if not r.get("lexical_match")]
        lexical_hits = [r for r in raw_results if r.get("lexical_match")]
        raw_results = trim_to_relevant(dense_hits) + trim_to_relevant(lexical_hits)
        if len(raw_results) != before_cliff:
            logger.info("Relevance cliff: %d -> %d results", before_cliff, len(raw_results))

        # Safety net — see services.magic_search_ranking.RELAXED_MIN_ROW. A short
        # row means the box had little to offer; the subject is still answerable
        # outside it, so ask again without the era (or the country) and append
        # what comes back. `outside_filters` travels with every one of those rows
        # so the UI can say which constraint was dropped — appending them
        # unmarked would be worse than the padding this replaces.
        relaxed_dimension = None
        if len(raw_results) < RELAXED_MIN_ROW:
            dimension = relaxable_dimension(intent)
            if dimension:
                seen_ids = {
                    int(r.get("metadata", {}).get("tmdb_id") or r["movie_id"])
                    for r in raw_results
                }
                extra = await qdrant.search_similar(
                    query_vector=query_vector,
                    limit=search_fetch_limit(intent),
                    score_threshold=0.3,
                    filters={
                        **relaxed_filters(qdrant_filters, dimension),
                        "exclude_tmdb_ids": list(seen_ids | set(watched_tmdb_ids)),
                    },
                )
                extra = trim_to_relevant(extra)
                if extra:
                    for r in extra:
                        r["outside_filters"] = dimension
                    relaxed_dimension = dimension
                    raw_results = raw_results + extra
                    logger.info(
                        "Relaxed %s: row was %d, now %d",
                        dimension, len(seen_ids), len(raw_results),
                    )

        # Minimum quality gate — drop movies with no TMDB signal (e.g. vote_count=0)
        #
        # Lee `vote_average` a secas otra vez: el 2026-08-17 hubo aquí una lectura
        # doble (`vote_average` o el nombre viejo `rating`) porque 931 puntos se
        # habían quedado en un esquema anterior y esta puerta los tiraba enteros.
        # El 2026-08-18 se reparó el DATO —`sync_qdrant_payload.py --fill-missing`,
        # 0 puntos sin `vote_average`— y el remiendo pasó a ser rama muerta.
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

            # Two numbers, not one: `relevance` (cosine → optional title boost)
            # is what the user sees, `relevance * weight` (VBS sigmoid gate) is
            # what orders the row. See services.magic_search_ranking.
            # compute_relevance for the full decision tree + thresholds. Pulled
            # out so the pipeline is testable without the FastAPI/Qdrant/DB stack.
            relevance, title_sim, quality_weight = compute_relevance(
                raw_cosine=r["score"],
                query=search_req.query,
                intent=intent,
                title=metadata.get("title") or "",
                vbs=(db_movie.vectorbox_score if db_movie else None),
            )
            if title_sim is not None and title_sim >= 0.85:
                logger.info(
                    f"Title-match boost for {metadata.get('title')} "
                    f"(sim={title_sim:.2f}): {relevance:.1f}"
                )

            result = {
                "movie_id": tmdb_id,
                "title": metadata.get("title", "Unknown"),
                "overview": metadata.get("overview", ""),
                "poster_path": poster_path,
                # RELEVANCE. The quality gate still ORDERS the row (`_rank`
                # below) but never reaches the UI, which renders this as distance
                # to the query — see compute_relevance for what showing the
                # gated value did to "muy bien valoradas".
                "score": round(relevance, 0),
                "_rank": relevance * quality_weight,  # precise float for sorting
                "outside_filters": r.get("outside_filters"),
                # True when the keyword channel is why this film is here. Not
                # rendered anywhere; it is what makes a surprising row debuggable
                # from the response alone instead of from the logs.
                "lexical_match": bool(r.get("lexical_match")),
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

        # Sprint 3 (2026-05-15): re-sort by relevance × quality weight so that
        # title-match boost and the VBS sigmoid gate actually affect ordering.
        # Before this, results came back in raw Qdrant cosine order — Qdrant is
        # now the initial filter / coarse rank and this is the final order.
        # Strip the internal `_rank` key before returning.
        # In-box films first, THEN the relaxed ones. Without the first key a
        # high-scoring out-of-era film outranks the films that met every
        # constraint, which is the opposite of what was asked for.
        results.sort(key=lambda r: (r.get("outside_filters") is not None,
                                    -r.get("_rank", 0.0)))
        # Truncate BEFORE the provider fan-out below: a post-filtered query now
        # fetches up to 150 candidates, and every survivor would otherwise cost a
        # provider lookup and a row in the response.
        del results[SEARCH_RESULT_LIMIT:]
        for r in results:
            r.pop("_rank", None)

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
        #
        # Signed-in only, as of 2026-07-30. `/try` has promised "no Tier-2 deep
        # analysis" in its docstring since it was written, and the promise was
        # never enforced anywhere: the trigger reads complexity and the request
        # flag, never the route, so any guest sentence with three filters —
        # "atracos con estilo, cine europeo de los 70" is exactly three — spent a
        # 120B call. That is the one cost `/try` is bounded on every other axis
        # to avoid. Caught by the Groq error surfacing inside
        # verify_search_branches.py, whose own docstring claims it never calls
        # Groq; it passes current_user=None, so this makes that true too.
        if (current_user is not None
                and should_run_deep_analysis(intent, user_requested=search_req.use_deep_analysis)
                and results):
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
                            # `score` is relevance and stays relevance. Pinning it
                            # to 100 here rendered every LLM-picked film as
                            # "d 0.00" — a perfect match by decree. `ai_reason`
                            # already carries the model's verdict.
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
            relaxed_filter=relaxed_dimension,
            watchlist_applied=bool(watchlist_ids),
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
    redis=Depends(get_redis),
):
    """Magic Box for signed-in users. Full budget.

    Auth is required again as of Fase 3 — the docstring said so all along while
    the signature said `get_optional_current_user`. Guests get `/try` below.
    """
    return await _run_natural_search(
        search_req, current_user, db, tmdb, qdrant, embedding_service, redis
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
    redis=Depends(get_redis),
):
    """The public door: type your own sentence without an account.

    Bounded on every axis that costs money — 140 characters, 5/minute, no Tier-2
    deep analysis, no forced_intent. A visitor can try the product; nobody can
    farm the daily Groq budget through it.

    `current_user=None` is passed explicitly rather than resolved: this route
    must behave identically for everyone, and reading a session here would make
    a signed-in user's results differ from a guest's on the same URL.
    """
    # `landing.query.free` del embudo, medido SIN endpoint público de métricas: esta
    # puerta es anónima por diseño y sólo la usa quien escribe su propia frase en la
    # landing, así que contar aquí es contar exactamente ese evento.
    await metrics.bump(redis, "landing.query.free")
    return await _run_natural_search(
        SearchRequest(query=try_req.query, country_code=try_req.country_code,
                      use_deep_analysis=False, forced_intent=None),
        None, db, tmdb, qdrant, embedding_service, redis,
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

    # `landing.query.chip` del embudo. Se cuenta el CLIC, antes de leer el caché: si
    # la fila estaba fría el visitante usó el chip igual, y el 503 ya se registra por
    # su lado. Los chips son el único consumidor de este endpoint, así que no hay
    # nada más que se confunda con ellos.
    await metrics.bump(redis, "landing.query.chip", lang=lang)

    if redis is None:
        raise HTTPException(status_code=503, detail="Showcase cache unavailable")

    payload = await showcase_service.read(redis, slug, lang)
    if payload is None:
        # Cold cache. The landing has a state for this; do not paper over it by
        # running a query, which is exactly what this endpoint exists to avoid.
        logger.warning("Showcase cache miss for slug=%s lang=%s — run warm_showcase.py", slug, lang)
        raise HTTPException(status_code=503, detail="Showcase not warmed yet")

    return payload


def _names_one_director(director_lists, lower: str) -> bool:
    """
    True when the term unambiguously names a single director: every catalogue row
    matched the same person, and a token of their name starts with the term.
    Both halves are load-bearing — the term is a substring match, so "man" matches
    Polanski, Mankiewicz and Forman, and "eve" matches St-eve-n Spielberg alone.
    """
    matched = {n for names in director_lists for n in (names or []) if lower in n.lower()}
    return len(matched) == 1 and any(
        part.lower().startswith(lower) for part in next(iter(matched)).split()
    )


@router.get("/autocomplete")
@limiter.limit("60/minute")
async def autocomplete_search(
    request: Request,
    q: str,
    tmdb: TMDBClient = Depends(get_tmdb_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Fast title autocomplete backing the More Like This search.
    Searches TMDB by title, and the catalogue by director, and merges the two.
    """
    term = q.strip()
    if len(term) < 2:
        return []

    async def by_title():
        data = await tmdb._make_request("/search/movie", {"query": term, "include_adult": "false"})
        return (data or {}).get("results") or []

    # Director search runs on our own rows — TMDB's /search/movie never matches a
    # director's name, so "kurosawa" returned junk or nothing. 99% of the
    # catalogue has `directors` populated; ordering by VBS puts the canon first.
    async def by_director():
        # `icontains(autoescape=True)` y no un f-string: interpolando el término, un
        # `%` del usuario entra como COMODIN. `%%%` se convertía en `%%%%%`, casaba
        # con todos los directores del catálogo y el desplegable contestaba con las
        # 8 películas de mejor VBS a una consulta que no pregunta nada.
        stmt = (
            select(Movie)
            .where(func.array_to_string(Movie.directors, "|").icontains(term, autoescape=True))
            .where(Movie.poster_path.isnot(None))
            .order_by(nulls_last(Movie.vectorbox_score.desc()))
            .limit(8)
        )
        return (await db.execute(stmt)).scalars().all()

    tmdb_rows, director_rows = await asyncio.gather(by_title(), by_director())

    # Erratas. TMDB no tiene búsqueda difusa y no la disimula: devuelve CERO, así
    # que una letra de más dejaba la searchbar vacía sin decir por qué
    # ("intersteller", "shawshenk redemption" → 0 resultados, medido 2026-08-10).
    #
    # Sólo cuando TMDB no ha traído NADA: en la ruta normal esto no se ejecuta, así
    # que no le cuesta latencia a quien escribe bien. Y sólo puede devolver
    # películas de nuestro catálogo, que es lo que sabemos describir de todas formas.
    fuzzy = False
    if not tmdb_rows:
        tmdb_rows = await _fuzzy_title_rows(db, term)
        fuzzy = bool(tmdb_rows)

    # A poster-less TMDB row is almost always a duplicate stub or a stray short
    # ranking above the real film on popularity alone. Drop them — but only while
    # something else survives, so a legitimately poster-less film is still findable.
    #
    # "something else" incluye la filmografía del director, y ahí estaba el fallo:
    # `lanthimos` devuelve UNA fila de TMDB, el stub sin estrenar ni póster
    # "Untitled Yorgos Lanthimos/Efthymis Filippou Project". El rescate lo
    # resucitaba y, como los títulos van antes que la filmografía, salía en el
    # puesto 1 por encima de Poor Things. Las prioridades de `_rank` no podían
    # arreglarlo: con una sola fila, ordenar no hace nada.
    posterful = [m for m in tmdb_rows if m.get("poster_path")]
    tmdb_rows = posterful or ([] if director_rows else tmdb_rows)

    # TMDB's order is lexical relevance, not popularity: "seven sa" put a 1-vote
    # 1966 Tagalog film above Seven Samurai. The candidate set is already
    # title-matched, so popularity alone is the right tiebreak — a title-prefix
    # bonus was tried and is worse ("GodFather" outranked The Godfather).
    lower = term.lower()
    # Por NIVELES: primero las que se llaman exactamente así, y dentro de cada
    # nivel por votos y luego popularidad.
    #
    # Ordenar todo por popularidad a secas se puso para arreglar "seven sa", donde
    # el orden de TMDB colaba un film tagalo de un voto por encima de Seven
    # Samurai. Pero rompía el caso contrario: "barrio" devuelve `Barrio` (1998,
    # León de Aranoa) en la POSICIÓN 3 del ranking de TMDB y con popularidad 1.0,
    # así que reordenar la hundía al puesto 14 y fuera del corte de ocho. La
    # película existía, TMDB la encontraba, y la escondíamos nosotros.
    #
    # Un bonus sumado a la popularidad ya se probó y era peor ("GodFather" ganaba
    # a The Godfather). Un nivel no es un bonus: no compite con la popularidad,
    # la precede. Y los votos van antes que la popularidad porque la popularidad
    # de TMDB decae con el tiempo y un clásico siempre pierde contra un estreno.
    def _rank(m):
        exact = (m.get("title") or "").strip().lower() == lower
        return (not exact, -(m.get("vote_count") or 0), -(m.get("popularity") or 0))

    # Las filas difusas vienen ya ordenadas por parecido, que es la única señal que
    # tiene sentido ahí: para "intersteller" no hay coincidencia exacta que premiar
    # y reordenar por votos pondría delante cualquier taquillazo que comparta
    # trigramas. `_rank` sólo manda cuando el título coincide de verdad.
    if not fuzzy:
        tmdb_rows.sort(key=_rank)

    # Reserve slots when the term is a director's name. "kurosawa" fills all 8
    # TMDB slots with documentaries *about* Kurosawa, so the films themselves
    # would land past the dropdown's cut and the feature would look broken.
    tmdb_take = 5 if director_rows else 8
    tmdb_rows = tmdb_rows[:tmdb_take]

    # Directors for the TMDB hits come from our catalogue; films we do not have
    # simply show no director rather than costing a /credits call each.
    tmdb_ids = [m["id"] for m in tmdb_rows]
    directors_by_id: dict = {}
    if tmdb_ids:
        rows = await db.execute(select(Movie.tmdb_id, Movie.directors).where(Movie.tmdb_id.in_(tmdb_ids)))
        directors_by_id = {tid: (d[0] if d else None) for tid, d in rows.all()}

    # El director se devuelve como ENTIDAD APARTE, no compitiendo en la lista.
    #
    # Antes, cuando el término nombraba a un director, sus películas se colaban
    # por delante de las coincidencias de título para que no se perdieran bajo el
    # tope de doce filas. Eso mezclaba dos cosas distintas en una sola ordenación
    # y obligaba a inventar cuánto "pesa" un director frente a una película —
    # pregunta sin buena respuesta. Con una tarjeta propia y una ficha a la que ir,
    # las películas vuelven a ordenarse sólo por relevancia y el reordenamiento
    # desaparece.
    lead_director = _names_one_director([mv.directors for mv in director_rows], lower)
    director_card = None
    if lead_director and director_rows:
        person_name = next(
            n for names in (mv.directors for mv in director_rows) for n in (names or [])
            if lower in n.lower()
        )
        total = await db.scalar(
            select(func.count()).select_from(
                select(Movie.id)
                .where(Movie.directors.any(person_name))
                .where(Movie.is_excluded.is_(False))
                .subquery()
            )
        )
        # La foto se pide aquí aunque esto sea la ruta de teclear-y-esperar.
        # Medido: `get_person` cuesta 374 ms en frío y 0 ms cacheado (30 días,
        # caché compartida), así que el sobrecoste se paga UNA vez por director
        # entre todos los usuarios. Y no se tira: quien ve la tarjeta suele abrir
        # la ficha a continuación, que ya encuentra foto y biografía calientes.
        # Es prefetch, no desperdicio.
        #
        # Si TMDB tarda o cae, la tarjeta sale sin foto: el cliente ya trae su
        # propio circuit breaker y el nombre es lo que identifica al director.
        profile_path = None
        try:
            person = await tmdb.get_person(person_name)
            profile_path = (person or {}).get("profile_path")
        except Exception as e:
            logger.warning("TMDB person lookup failed for %r: %s", person_name, e)
        director_card = {
            "name": person_name,
            "film_count": total or 0,
            "profile_path": profile_path,
        }

    tmdb_out, director_out, seen = [], [], set()
    for m in tmdb_rows:
        seen.add(m["id"])
        tmdb_out.append({
            "tmdb_id": m["id"],
            "title": m["title"],
            "year": int(m["release_date"][:4]) if m.get("release_date") else None,
            "poster_path": m.get("poster_path"),
            "overview": m.get("overview", ""),
            "director": directors_by_id.get(m["id"]),
        })
    for mv in director_rows:
        if mv.tmdb_id in seen or len(tmdb_out) + len(director_out) >= 12:
            continue
        seen.add(mv.tmdb_id)
        director_out.append({
            "tmdb_id": mv.tmdb_id,
            "title": mv.title,
            "year": mv.year,
            "poster_path": mv.poster_path,
            "overview": mv.overview or "",
            "director": mv.directors[0] if mv.directors else None,
        })
    # Cuando el término nombra a UN director sin ambigüedad, su filmografía va
    # primera. No son dos coincidencias del mismo tipo compitiendo por relevancia:
    # una contesta "una película que se LLAMA así" y la otra "películas DE quien se
    # llama así", y ordenarlas juntas obliga a inventar cuánto vale un título frente
    # a un autor — pregunta sin buena respuesta.
    #
    # Con "kurosawa" la respuesta por título son cinco documentales SOBRE él (uno es
    # `Hitomi Kurosawa: Clumsy Love Story`) y Seven Samurai caía al sexto puesto.
    # Nadie escribe "kurosawa" buscando eso. La reserva de huecos ya evitaba que la
    # filmografía se cayera del desplegable; lo que faltaba era el orden.
    #
    # `director_card` sólo existe si `_names_one_director` confirmó que el término
    # apunta a una sola persona, así que esto no se dispara con un apellido
    # compartido: ahí siguen mandando los títulos.
    if director_card:
        return {"director": director_card, "films": director_out + tmdb_out}
    return {"director": director_card, "films": tmdb_out + director_out}

