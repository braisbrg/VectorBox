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
from typing import Iterable, Optional

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
    )


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


# --- 4. blended score --------------------------------------------------------


def compute_blended_score(
    raw_cosine: float,
    query: str,
    intent: MovieSearchIntent,
    title: str,
    vbs: Optional[float],
) -> tuple[float, Optional[float], float]:
    """The score the front-end actually sees and the ranker sorts by.

    Returns (final_score, title_sim_or_None, quality_weight). The two extras
    are useful for debug payloads and the validation script.
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
    return score * weight, ts, weight


# --- 5. post-filter ---------------------------------------------------------


def movie_passes_post_filter(movie: Movie, intent: MovieSearchIntent) -> bool:
    """Return True if `movie` clears every Sprint-1+2 post-filter dimension
    that the intent has populated.

    Kept pure (no DB / no Qdrant) so the test suite can pin every branch
    against synthetic Movie objects.
    """
    if intent.safe_mode and bool(movie.is_adult):
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

    return True


# --- 6. should_run_deep_analysis --------------------------------------------


DEEP_ANALYSIS_COMPLEXITY_THRESHOLD = 3


def should_run_deep_analysis(intent: MovieSearchIntent, user_requested: bool = False) -> bool:
    """Tier-2 LLM trigger. Explicit user opt-in always wins; otherwise the
    complexity heuristic decides."""
    if user_requested:
        return True
    return intent_complexity(intent) >= DEEP_ANALYSIS_COMPLEXITY_THRESHOLD
