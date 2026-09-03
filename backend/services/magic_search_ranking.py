"""Pure scoring / filtering helpers for Magic Search.

Extracted from `routers/search.py` so each piece of the post-Qdrant pipeline
can be exercised without spinning up the full FastAPI request — the route
handler glues these together with the embedding model, the Qdrant client,
and the SQLAlchemy session, but the *decisions* (which films pass, how
they're scored, when to trigger Tier-2 LLM) live here as plain functions.

Everything in this module is synchronous and side-effect-free. Inputs are
the parsed `MovieSearchIntent`, the candidate Movie rows, and the original
query string. Outputs are filter verdicts, complexity counts, and blended
scores — no DB, no Qdrant, no HTTP.
"""
from __future__ import annotations

import math
from difflib import SequenceMatcher
from typing import Optional

from models.database import Movie
from services.nlp_search import MovieSearchIntent
from utils.scoring import normalize_similarity_score


# --- Quality gate parameters -------------------------------------------------

# Catalog VBS median sits around 55, so we anchor the sigmoid midpoint there.
# Floor (0.20) keeps a strong vector match alive even for low-VBS films, so
# legitimate cult/foreign/obscure cinema isn't fully zeroed.
QUALITY_MIDPOINT_DEFAULT = 55
QUALITY_MIDPOINT_BYPASS = 25   # `quality_gate_bypass=True` softens the gate
QUALITY_STEEPNESS = 0.10
QUALITY_FLOOR_DEFAULT = 0.20
QUALITY_FLOOR_BYPASS = 0.10

# --- confidence ---------------------------------------------------------------
#
# The engine used to fill the shelf whatever it found: 14 films whether the best
# neighbour scored 0.65 or 0.19. That is how "receta de tortilla de patatas"
# answered with Ratatouille and "342342 8888 ????" with Werckmeister Harmonies —
# both perfectly confident, both nonsense as recommendations.
#
# Threshold measured, not guessed. scripts/experiment_confidence.py runs an
# 18-query panel (thematic / audience-fit / too-vague / nonsense / off-domain)
# three times through the real pipeline. Over 54 runs:
#
#   statistic      min answerable   max unanswerable   margin
#   raw_max                 0.470              0.454   +0.016
#   raw_mean@10             0.443              0.425   +0.018   <- widest, and a
#   kept_mean               0.435              0.422   +0.013      mean is steadier
#   vbs_mean                54.98              78.26   -23.28      than one max
#
# vbs_mean is worse than useless: nonsense returns ACCLAIMED films, so ranking by
# quality when similarity is low would dress gibberish in prestige and look
# deliberate. Similarity is the only honest signal here.
#
# Set at the top of the unanswerable range rather than the middle of the margin,
# on purpose: refusing a real question is a worse failure than answering a silly
# one, so the gate leans towards answering.
LOW_CONFIDENCE_MEAN = 0.43

# Floor for the "I don't know what to watch" answer. High on purpose: this is the
# one case where the user has explicitly delegated the choice, so the selection
# should be films the catalogue is confident about, not the merely acceptable.
OPEN_REQUEST_MIN_VBS = 80
CONFIDENCE_SAMPLE = 10


def search_confidence(raw_cosines: list[float]) -> float:
    """Mean cosine of the top neighbours — how well the catalogue matches at all.

    Computed BEFORE post-filtering: filters remove films for reasons unrelated to
    whether the question made sense (era, rating, already seen), and a query that
    filters down to two good films is narrow, not unanswerable.
    """
    if not raw_cosines:
        return 0.0
    top = sorted(raw_cosines, reverse=True)[:CONFIDENCE_SAMPLE]
    return sum(top) / len(top)


def is_low_confidence(raw_cosines: list[float]) -> bool:
    """True when the catalogue has nothing close enough to be a recommendation."""
    return search_confidence(raw_cosines) < LOW_CONFIDENCE_MEAN

# --- relevance cliff ---------------------------------------------------------
#
# A filtered search ALWAYS returns its nearest neighbours, however far away they
# are. That is how "atracos con estilo, cine europeo de los 70" shipped a row of
# twenty with two heists in it: inside a box of 442 films the nearest twenty are
# just the most 70s-Euro-crime-ish things in the box, and nothing downstream
# asked whether they were close enough to be an answer.
#
# What this is NOT: a judgement about the query. Three candidate detectors were
# measured on a 12-query panel (2026-07-31) and every one of them FAILED to
# separate answerable from unanswerable — absolute cosine, the cosine recovered
# by dropping filters, and keyword overlap all put a good case on the wrong side:
#
#   abs     kung fu 70s (good) 0.477  <  found footage 60s (bad) 0.557
#   drop    atracos EU (bad)  +0.000  <  anime 90s (good)       +0.091
#   overlap superheroes 60s (bad) 100% > thrillers coreanos (good) 10%
#
# So no gate classifies the question. This trims the TAIL instead, relative to
# the best neighbour this query actually found, which cannot be wrong about a
# query because it never removes the head. Measured survivors at 0.85:
#
#   padded  cyberpunk 50s 20->7   zombis 40s 20->6   atracos EU 20->15
#   real    giallo 20  noir 20  grief 20  heists-70s 20  korean 20
#           slasher 17  anime 90s 12  kung fu 70s 9
#
# 0.88 starts gutting real answers (giallo 20->10, kung fu 20->5); 0.80 barely
# trims anything (cyberpunk keeps 18). The worst case at 0.85 is a legitimate
# query served 9 films instead of 20 — a smaller row, never a blank page.
#
# Applied to the RAW cosine, before the VBS blend, so relevance decides who is
# in the answer and quality only decides the order within it.
RELEVANCE_CLIFF = 0.85


def trim_to_relevant(raw_results: list[dict]) -> list[dict]:
    """Drop neighbours far below the best one this query found."""
    if not raw_results:
        return raw_results
    best = max((r.get("score") or 0.0) for r in raw_results)
    if best <= 0:
        return raw_results
    floor = best * RELEVANCE_CLIFF
    return [r for r in raw_results if (r.get("score") or 0.0) >= floor]


# --- safety net: keep the subject, relax the modifier ------------------------
#
# Some requests cannot be satisfied because the films do not exist. "cyberpunk de
# los 50" is the clean example: the catalogue holds three 1950s films with any
# adjacent keyword (Forbidden Planet, The War of the Worlds, On the Beach) and
# none is cyberpunk, because the genre starts around 1982 — Blade Runner sits in
# the control group one decade later. Nothing is broken there; the answer simply
# is not in the world, let alone the catalogue.
#
# Padding the row with the nearest 1950s films is the wrong response, and so is a
# blank page. What a person asking for "cyberpunk de los 50" wants is cyberpunk:
# the SUBJECT is the request, the era is a preference. So when the row comes back
# short, the same theme is searched again with the era dropped, and those films
# are appended MARKED, never silently mixed in.
#
# Triggered by the LENGTH OF THE ROW, not by a judgement about the query — same
# reason as the cliff, since three query-level detectors were measured and none
# separated answerable from unanswerable. Measured row lengths after the cliff:
#
#   real answers     kung fu 70s 9 · giallo 10 · anime 90s 12 · atracos EU 14
#   nothing there    cyberpunk 50s 7 · zombis 40s 5
#
# 8 sits in that gap. Being wrong is cheap in both directions: too eager appends
# clearly-labelled extras to a row that was already fine, too shy leaves today's
# behaviour. Neither can empty a page or hide the films the user did ask for.
RELAXED_MIN_ROW = 8


def relaxable_dimension(intent: MovieSearchIntent) -> Optional[str]:
    """Which filter to drop when the row is too short, or None.

    Era first: it is the dimension least likely to have a thematic correlate in
    the catalogue, and the one a viewer trades away most readily. Country second.
    Genre, runtime and the quality bars are never relaxed — those are the request
    itself, not a preference around it.
    """
    if intent.year_min or intent.year_max:
        return "era"
    if intent.countries:
        return "countries"
    return None


def relaxed_filters(qdrant_filters: dict, dimension: str) -> dict:
    """`qdrant_filters` minus the relaxed dimension."""
    dropped = {"era": ("year_min", "year_max"), "countries": ("countries",)}[dimension]
    return {k: v for k, v in qdrant_filters.items() if k not in dropped}


# --- Title-boost parameters --------------------------------------------------

TITLE_BOOST_QUERY_MAX_LEN = 40
TITLE_BOOST_MIN_SIM = 0.85
TITLE_BOOST_VECTOR_WEIGHT = 0.70   # 70% semantic, 30% title


# --- 1. intent_complexity ----------------------------------------------------


def intent_complexity(intent: MovieSearchIntent) -> int:
    """Count of populated filter dimensions.

    Used as the Deep-Analysis auto-trigger: queries that fill ≥ 3 fields
    almost always benefit from Tier-2 LLM nuance, and Groq Patron makes the
    extra call effectively free. `popularity_vibe != "any"` counts because
    it constrains the result set.
    """
    n = sum(
        1 for v in (
            intent.include_genres, intent.year_min, intent.year_max,
            intent.min_runtime_minutes, intent.max_runtime_minutes,
            intent.min_rating, intent.original_language,
            intent.reference_movie, intent.mpaa_ratings,
            intent.min_oscar_wins, intent.min_imdb_rating,
            intent.min_metacritic, intent.countries,
            intent.spoken_languages, intent.awards_contains,
        ) if v
    )
    if intent.popularity_vibe != "any":
        n += 1
    return n


# --- 2. title_boost_eligible -------------------------------------------------


def has_descriptive_filters(intent: MovieSearchIntent) -> bool:
    """True if the intent populates any filter that signals a descriptive
    query (the user is describing what they want, not naming a specific
    title)."""
    return bool(
        intent.year_min or intent.year_max or intent.include_genres
        or intent.min_runtime_minutes or intent.max_runtime_minutes
        or intent.min_rating or intent.original_language
        or intent.mpaa_ratings or intent.min_oscar_wins
        or intent.min_imdb_rating or intent.min_metacritic
        or intent.countries or intent.spoken_languages
        or intent.awards_contains
        # min_vectorbox_score was missing (added 2026-07-29): a query whose only
        # criterion was quality did not count as "descriptive", so the confidence
        # gate refused it even though the catalogue could answer it perfectly.
        or intent.min_vectorbox_score
    )


# --- how many candidates to ask Qdrant for ----------------------------------
#
# A dimension enforced in Postgres AFTER the search can only ever keep a subset
# of the twenty nearest neighbours of the query vector, and for a filter
# orthogonal to the theme that subset is usually empty: measured 2026-07-29,
# "thrillers coreanos" parsed correctly to countries=['South Korea'] and kept
# ONE film out of twenty generic "thriller, suspense, mystery" neighbours, out
# of the 219 Korean films the catalogue holds.
#
# Over-fetching was the cheap half of the fix and bought 1 -> 3. The real half
# landed the same day: countries, spoken_languages and min_vectorbox_score moved
# INTO the Qdrant payload, so they narrow during the search — 20 of 20 at the
# default fetch. Only awards_contains is left needing the wide read.
SEARCH_FETCH_DEFAULT = 20
SEARCH_FETCH_POST_FILTERED = 150
SEARCH_RESULT_LIMIT = 20


def has_post_filters(intent: MovieSearchIntent) -> bool:
    """True when a filter dimension is enforced in Postgres rather than Qdrant.

    Down to one as of 2026-07-29. countries, spoken_languages and
    min_vectorbox_score moved into the Qdrant payload, so they now narrow DURING
    the search and need no headroom at all: "thrillers coreanos" went from 1
    Korean film among twenty candidates to 20 of 20.

    awards_contains stays because it is a SUBSTRING match over free text
    ("Won 3 Oscars"), which needs a full-text payload index rather than a keyword
    one — a different piece of work, and the rarest of the five.
    """
    return bool(intent.awards_contains)


def search_fetch_limit(intent: MovieSearchIntent) -> int:
    return SEARCH_FETCH_POST_FILTERED if has_post_filters(intent) else SEARCH_FETCH_DEFAULT


def is_quality_only_request(intent: MovieSearchIntent) -> bool:
    """A quality bar and no subject — "peliculas muy bien valoradas", "algo muy
    aclamado por la critica".

    These have no usable vector (measured 0.355 and 0.308) but are perfectly
    answerable: the bar IS the query. Answering them from the twenty nearest
    neighbours of a meaningless vector is how "algo muy aclamado por la critica"
    returned three films — the filter is a hard AND over whatever survived a 0.3
    cosine threshold, and almost nothing survives it.

    Whether the parser reaches for min_vectorbox_score or min_metacritic on the
    same sentence is a coin flip, and it decided whether the user got twelve
    films or three. This asks the question the branch actually cares about —
    is there a bar and nothing else — instead of naming one field.

    Metacritic is the worst of the bars to hold a query up on: it covers 54.5% of
    the catalogue (measured 2026-07-29), so a film with no score fails the filter
    however good it is. VBS is computed for everything and already folds
    Metacritic in where it exists, which is why the catalogue branch ranks on it.
    """
    if intent.open_request or intent.reference_movie:
        return False
    has_bar = any((
        intent.min_vectorbox_score, intent.min_rating,
        intent.min_imdb_rating, intent.min_metacritic, intent.min_oscar_wins,
    ))
    has_subject = any((
        intent.include_genres, intent.year_min, intent.year_max,
        intent.min_runtime_minutes, intent.max_runtime_minutes,
        intent.original_language, intent.mpaa_ratings, intent.countries,
        intent.spoken_languages, intent.awards_contains,
    ))
    return has_bar and not has_subject


def title_boost_eligible(intent: MovieSearchIntent, query: str) -> bool:
    """Is the user doing a literal-title lookup? Gates the title-match boost.

    Four conditions must all hold:
      1. No `reference_movie` — LLM hasn't tagged "movies like X".
      2. No descriptive filters populated.
      3. Query is short (≤ 40 chars) — long queries are never title lookups.
      4. (The caller still checks title_sim ≥ 0.85 per candidate.)

    The bar is high because Sprint-3 made this boost actually affect order,
    and the "Deprisa, deprisa → Fast and Furious" class of error becomes
    one nudge away when the gate is too permissive.
    """
    if intent.reference_movie:
        return False
    if has_descriptive_filters(intent):
        return False
    if len(query.strip()) > TITLE_BOOST_QUERY_MAX_LEN:
        return False
    return True


def title_sim_score(query: str, title: str) -> float:
    """SequenceMatcher ratio between lowercased query and title. Pulled out
    so the validation script and tests can reproduce it."""
    return SequenceMatcher(None, query.lower(), (title or "").lower()).ratio()


# --- 3. quality_gate_weight --------------------------------------------------


def quality_gate_weight(vbs: Optional[float], quality_gate_bypass: bool) -> float:
    """Sigmoid weight applied to the score based on `vectorbox_score`.

    NULL VBS is treated as 0 (lowest known quality) so films with no
    OMDb/TMDB vote signal don't bypass the gate — pre-Sprint-3 they did,
    and crowded the top of any Magic Search answer.
    """
    if quality_gate_bypass:
        midpoint, floor = QUALITY_MIDPOINT_BYPASS, QUALITY_FLOOR_BYPASS
    else:
        midpoint, floor = QUALITY_MIDPOINT_DEFAULT, QUALITY_FLOOR_DEFAULT
    vbs_effective = vbs if vbs is not None else 0
    sigmoid = 1.0 / (1.0 + math.exp(-QUALITY_STEEPNESS * (vbs_effective - midpoint)))
    return floor + (1.0 - floor) * sigmoid


# --- 4. relevance + quality weight -------------------------------------------


def compute_relevance(
    raw_cosine: float,
    query: str,
    intent: MovieSearchIntent,
    title: str,
    vbs: Optional[float],
) -> tuple[float, Optional[float], float]:
    """Returns (relevance, title_sim_or_None, quality_weight).

    RANK by `relevance * weight`, DISPLAY `relevance`. Two different questions:
    "how close is this to what you asked" and "which of these do we put first".
    VBS answers the second and has no business in the first — "muy bien
    valoradas" showed 99 while a precise, correctly-answered giallo query showed
    83, because the vague query matched acclaimed films (2026-07-31).

    This used to return the product and the route divided the weight back out to
    display it, which is the same two numbers with a cancellation in between.
    """
    score = normalize_similarity_score(raw_cosine)

    ts: Optional[float] = None
    if title_boost_eligible(intent, query):
        ts = title_sim_score(query, title)
        if ts >= TITLE_BOOST_MIN_SIM:
            title_score = 90 + (ts * 9)
            score = (
                TITLE_BOOST_VECTOR_WEIGHT * score
                + (1.0 - TITLE_BOOST_VECTOR_WEIGHT) * title_score
            )

    weight = quality_gate_weight(vbs, intent.quality_gate_bypass)
    return score, ts, weight


# --- 5. post-filter ---------------------------------------------------------


def movie_passes_post_filter(movie: Movie, intent: MovieSearchIntent) -> bool:
    """Return True if `movie` clears every Sprint-1+2 post-filter dimension
    that the intent has populated.

    Kept pure (no DB / no Qdrant) so the test suite can pin every branch
    against synthetic Movie objects.
    """
    if intent.safe_mode and bool(movie.is_adult):
        return False

    # A film nobody can watch yet is not a recommendation. Found 2026-07-30 while
    # measuring the landing: "atracos con estilo" returned `Untitled Ocean's
    # Prequel (2027)` in the top twelve. 184 unreleased films carry a searchable
    # vector, so this is not a one-off. rss.py:573 already refuses them for the
    # same reason; the upcoming RAIL keeps its own query and is unaffected.
    if movie.is_upcoming:
        return False

    if intent.mpaa_ratings is not None:
        allowed = set(intent.mpaa_ratings)
        if (movie.mpaa_rating or "") not in allowed:
            return False

    if intent.min_oscar_wins and (movie.oscar_wins or 0) < intent.min_oscar_wins:
        return False

    if intent.countries is not None:
        wanted = set(intent.countries)
        if set(movie.omdb_countries or []).isdisjoint(wanted):
            return False

    if intent.spoken_languages is not None:
        wanted = set(intent.spoken_languages)
        if set(movie.omdb_languages or []).isdisjoint(wanted):
            return False

    if intent.awards_contains:
        haystack = (movie.awards_text or "").lower()
        needles = [s.lower() for s in intent.awards_contains]
        if not all(n in haystack for n in needles):
            return False

    if intent.min_vectorbox_score is not None and (movie.vectorbox_score or 0) < intent.min_vectorbox_score:
        return False

    return True


# --- 6. should_run_deep_analysis --------------------------------------------


DEEP_ANALYSIS_COMPLEXITY_THRESHOLD = 3


def should_run_deep_analysis(intent: MovieSearchIntent, user_requested: bool = False) -> bool:
    """Tier-2 LLM trigger. Explicit user opt-in always wins; otherwise the
    complexity heuristic decides."""
    if user_requested:
        return True
    return intent_complexity(intent) >= DEEP_ANALYSIS_COMPLEXITY_THRESHOLD
