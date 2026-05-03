"""
Onboarding Router — Guest carousel flow + tag preferences.

Public endpoints:
    GET  /movies    — 15 diverse films for the onboarding carousel (optional auth)

Auth-required endpoints:
    POST /migrate-guest — Migrate localStorage ratings/tags to Postgres
    POST /tags          — Save tag preferences (Settings UI)
    GET  /status        — Onboarding completion status
"""
import logging
import random
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy import String, cast

from config import get_db, REDIS_URL, AsyncSessionLocal
from dependencies import get_current_user, get_optional_current_user, get_qdrant_service
from models.database import User, Movie, UserRating
from models.schemas import TokenResponse
from services.recommendation_engine import MOVIE_QUALITY_GATE
from services.profile_cache import set_profile_dirty
from services.qdrant_service import QdrantService

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 5 genre poles — one movie per pole guarantees visual + genre diversity
# in the first 5 slots of the carousel. Each pole carries optional
# `genres` (ARRAY-overlap filter) and `filters` (column-level constraints).
GENRE_POLES = {
    "arthouse": {
        "filters": {"max_popularity": 15, "min_vote_count": 5000},
        "description": "Art house / slow cinema",
    },
    "blockbuster": {
        "genres": ["Action", "Adventure"],
        "filters": {"min_popularity": 50, "min_vote_count": 10000},
        "description": "Mainstream blockbusters",
    },
    "horror": {
        "genres": ["Horror"],
        "filters": {"min_vote_count": 5000},
        "description": "Horror films",
    },
    "sci_fi": {
        "genres": ["Science Fiction"],
        "filters": {"min_vote_count": 5000},
        "description": "Science fiction",
    },
    "intl": {
        "filters": {"original_language_not_en": True, "min_vote_count": 8000},
        "description": "International / non-English cinema",
    },
}

# Tag → SQL filter mapping. Aligned with product spec.
# When a tag is AVOIDED, `exclude_genres` (or `include_genres` as fallback) drives
# the WHERE-NOT clause. Tags with empty configs are stored in prefs but produce
# no SQL constraint (kept for future expansion / non-genre signal).
TAG_FILTERS = {
    "Jumpscares": {"include_genres": ["Horror"]},
    "Gore": {"include_genres": ["Horror"]},
    "Terror psicológico": {"include_genres": ["Horror", "Thriller"]},
    "Contenido adulto": {},  # no direct genre filter for v1
    "Temáticas oscuras": {"include_genres": ["Drama", "Thriller"]},
    "Ritmo muy lento": {"min_popularity": 20},
    "Películas +3h": {"max_runtime": 180},
    "Animación": {"include_genres": ["Animation"]},
    "Documentales": {"include_genres": ["Documentary"]},
    "Mudas / B&N": {},  # no direct filter for v1
    "Musicales": {"include_genres": ["Music"]},
    "Contenido familiar": {"include_genres": ["Family"]},
    "Ciencia ficción dura": {},  # no direct filter for v1
    "Basadas en hechos reales": {},  # no direct filter for v1
    "Cine de superhéroes": {"exclude_keywords": ["superhero", "marvel", "dc comics"]},
}

TAG_WHITELIST = set(TAG_FILTERS.keys())

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class MigrateGuestRequest(BaseModel):
    ratings: Dict[int, str]  # tmdb_id → "positive"|"neutral"|"negative"
    tags: Dict[str, List[str]]  # {"avoided": [...]}

class TagsRequest(BaseModel):
    avoided: List[str]

class OnboardingMovie(BaseModel):
    tmdb_id: int
    title: str
    year: Optional[int] = None
    poster_path: Optional[str] = None
    overview: Optional[str] = None
    genres: Optional[List[str]] = None
    vote_average: Optional[float] = None
    vote_count: Optional[int] = None
    runtime: Optional[int] = None
    original_language: Optional[str] = None
    vectorbox_score: Optional[float] = None


def _apply_diversity_filters(candidates: List[Movie], limit: int) -> List[Movie]:
    """
    Apply diversity caps:
    - Max 2 per genre per window of 5
    - Max 2 per decade per window of 5
    Returns up to `limit` diverse movies.
    """
    selected = []
    genre_counts: Dict[str, int] = {}
    decade_counts: Dict[int, int] = {}
    
    for movie in candidates:
        if len(selected) >= limit:
            break
        
        # Check genre cap (max 2 per genre)
        movie_genres = movie.genres or []
        genre_ok = all(genre_counts.get(g, 0) < 2 for g in movie_genres)
        
        # Check decade cap (max 2 per decade)
        decade = (movie.year // 10 * 10) if movie.year else 0
        decade_ok = decade_counts.get(decade, 0) < 2
        
        if genre_ok and decade_ok:
            selected.append(movie)
            for g in movie_genres:
                genre_counts[g] = genre_counts.get(g, 0) + 1
            decade_counts[decade] = decade_counts.get(decade, 0) + 1
    
    return selected

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIGNAL_TO_RATING = {
    "positive": 4.5,
    "neutral": 3.0,
    "negative": 1.5,
}


def _movie_to_dict(movie: Movie) -> dict:
    return OnboardingMovie(
        tmdb_id=movie.tmdb_id,
        title=movie.title,
        year=movie.year,
        poster_path=movie.poster_path,
        overview=movie.overview,
        genres=movie.genres,
        vote_average=movie.vote_average,
        vote_count=movie.vote_count,
        runtime=movie.runtime,
        original_language=movie.original_language,
        vectorbox_score=movie.vectorbox_score,
    ).model_dump()


def _apply_tag_exclude_filters(query, avoided_tags: List[str]):
    """Translate avoided tag keys into WHERE-NOT clauses.

    For each avoided tag, prefer `exclude_genres`; fall back to `include_genres`
    (semantically: "the genre this tag *labels* — exclude it"). Also respects
    column-level filters such as `max_runtime` and `min_popularity`.
    """
    logger.info(f"[Onboarding] Applying tag filters. Avoided: {avoided_tags}")
    for tag in avoided_tags:
        config = TAG_FILTERS.get(tag, {})
        genres_to_exclude = config.get("exclude_genres") or config.get("include_genres", [])
        if genres_to_exclude:
            query = query.where(
                ~Movie.genres.op('&&')(cast(genres_to_exclude, ARRAY(String)))
            )
        if config.get("max_runtime"):
            query = query.where(Movie.runtime <= config["max_runtime"])
        if config.get("min_popularity"):
            query = query.where(Movie.popularity >= config["min_popularity"])
    return query


# ---------------------------------------------------------------------------
# GET /movies — 15 diverse films for the carousel (optional auth)
# ---------------------------------------------------------------------------

@router.get("/movies")
async def get_onboarding_movies(
    country_code: str = "ES",
    avoided_tags: str = "",
    page: int = 1,
    exclude_ids: str = "",
    db: AsyncSession = Depends(get_db),
    current_user: Optional[TokenResponse] = Depends(get_optional_current_user),
):
    """
    Return 15 movies for the onboarding carousel.
    First 5: one per genre pole (guaranteed diversity).
    Next 10: diverse pool with genre/decade caps.
    Guest-safe (no auth required). If authed, excludes already-rated movies.
    """
    # Build base exclusion set
    rated_movie_ids: set = set()
    tag_prefs: dict = {}

    if current_user:
        # Exclude movies the user has already rated
        rated_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == current_user.user_id)
        )
        rated_internal_ids = set(rated_result.scalars().all())
        if rated_internal_ids:
            # Convert to tmdb_ids for consistent filtering
            tmdb_result = await db.execute(
                select(Movie.tmdb_id).where(Movie.id.in_(rated_internal_ids))
            )
            rated_movie_ids = set(tmdb_result.scalars().all())

        # Load tag preferences for filtering
        user_result = await db.execute(
            select(User.tag_preferences).where(User.id == current_user.user_id)
        )
        tag_prefs = user_result.scalar_one_or_none() or {}

    if current_user and tag_prefs:
        avoided_tags_list = tag_prefs.get("avoided", [])
    elif avoided_tags:
        avoided_tags_list = [t.strip() for t in avoided_tags.split(",") if t.strip()]
    else:
        avoided_tags_list = []

    if page > 1:
        shown_ids = [int(x) for x in exclude_ids.split(",") if x.strip()]
        
        # Get diverse pool excluding already shown
        pool_query = (
            select(Movie)
            .where(*MOVIE_QUALITY_GATE)
            .where(Movie.tmdb_id.notin_(shown_ids))
            .where(Movie.vote_count >= 500)
            .where(Movie.vectorbox_score >= 55)
            .where(Movie.poster_path.isnot(None))
        )
        
        if avoided_tags_list:
            pool_query = _apply_tag_exclude_filters(pool_query, avoided_tags_list)
            
        pool_query = pool_query.order_by(desc(Movie.popularity)).limit(100)
        result = await db.execute(pool_query)
        candidates = result.scalars().all()
        
        if avoided_tags_list:
            logger.info(f"[Onboarding] After tag filter: {len(candidates)} candidates remaining")
            
        selected = _apply_diversity_filters(candidates, limit=15)
        return [_movie_to_dict(m) for m in selected]

    # --- Phase 1: One movie per genre pole (5 movies) ---
    pole_movies: list = []
    pole_tmdb_ids: set = set()

    for pole_id, pole_config in GENRE_POLES.items():
        query = (
            select(Movie)
            .where(*MOVIE_QUALITY_GATE)
            .where(Movie.vectorbox_score >= 55)
            .where(Movie.poster_path.isnot(None))
        )

        # Optional genre filter (some poles intentionally don't constrain genre)
        if pole_config.get("genres"):
            genre_array = cast(pole_config["genres"], ARRAY(String))
            query = query.where(Movie.genres.overlap(genre_array))

        # Per-pole column filters
        filters = pole_config.get("filters", {})
        if filters.get("min_vote_count"):
            query = query.where(Movie.vote_count >= filters["min_vote_count"])
        if filters.get("max_popularity") is not None:
            query = query.where(Movie.popularity <= filters["max_popularity"])
        if filters.get("min_popularity") is not None:
            query = query.where(Movie.popularity >= filters["min_popularity"])
        if filters.get("original_language_not_en"):
            query = query.where(Movie.original_language != "en")

        if rated_movie_ids:
            query = query.where(Movie.tmdb_id.notin_(rated_movie_ids))
        if pole_tmdb_ids:
            query = query.where(Movie.tmdb_id.notin_(pole_tmdb_ids))
        if avoided_tags_list:
            query = _apply_tag_exclude_filters(query, avoided_tags_list)

        query = query.order_by(desc(Movie.vectorbox_score)).limit(5)
        result = await db.execute(query)
        candidates = result.scalars().all()

        if candidates:
            # Pick randomly from top 5 to add variety across sessions
            chosen = random.choice(candidates)
            pole_movies.append(chosen)
            pole_tmdb_ids.add(chosen.tmdb_id)

    # --- Phase 2: 10 diverse movies from a wider pool ---
    seen_tmdb_ids = pole_tmdb_ids | rated_movie_ids
    pool_query = (
        select(Movie)
        .where(*MOVIE_QUALITY_GATE)
        .where(Movie.vote_count >= 500)
        .where(Movie.vectorbox_score >= 55)
        .where(Movie.poster_path.isnot(None))
    )
    if seen_tmdb_ids:
        pool_query = pool_query.where(Movie.tmdb_id.notin_(seen_tmdb_ids))
    if avoided_tags_list:
        pool_query = _apply_tag_exclude_filters(pool_query, avoided_tags_list)

    pool_query = pool_query.order_by(
        desc(Movie.vectorbox_score)
    ).limit(80)

    pool_result = await db.execute(pool_query)
    pool_candidates = pool_result.scalars().all()

    if avoided_tags_list:
        logger.info(f"[Onboarding] After tag filter: {len(pool_candidates)} candidates remaining in diverse pool")

    diverse_picks = _apply_diversity_filters(pool_candidates, limit=10)

    # Combine: 5 pole + 10 diverse
    all_movies = pole_movies + diverse_picks
    return [_movie_to_dict(m) for m in all_movies]


# ---------------------------------------------------------------------------
# POST /migrate-guest — Migrate localStorage ratings + tags to Postgres
# ---------------------------------------------------------------------------

@router.post("/migrate-guest")
async def migrate_guest(
    body: MigrateGuestRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user),
    qdrant: QdrantService = Depends(get_qdrant_service),
):
    """
    Migrate guest localStorage ratings + tags to the authenticated user's profile.
    Idempotency: if the user already has ratings, return skipped.
    """
    user_id = current_user.user_id

    # Idempotency guard
    existing_count = await db.scalar(
        select(func.count(UserRating.id)).where(UserRating.user_id == user_id)
    )
    if existing_count and existing_count > 0:
        return {"status": "skipped", "reason": "user already has ratings"}

    # Save tag preferences
    tag_data = {
        "avoided": body.tags.get("avoided", []),
    }
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.tag_preferences = tag_data

    # Map signal → rating and insert UserRatings
    migrated = 0
    for tmdb_id_str, signal in body.ratings.items():
        tmdb_id = int(tmdb_id_str)
        rating_value = SIGNAL_TO_RATING.get(signal)
        if rating_value is None:
            continue

        # Look up Movie by tmdb_id — don't create bare movies (AGENTS.md anti-pattern #20)
        movie_result = await db.execute(
            select(Movie).where(Movie.tmdb_id == tmdb_id)
        )
        movie = movie_result.scalar_one_or_none()
        if not movie:
            logger.warning(f"[migrate-guest] Skipping tmdb_id={tmdb_id}: not in DB")
            continue

        user_rating = UserRating(
            user_id=user_id,
            movie_id=movie.id,
            rating=rating_value,
            is_watched=True,
            watch_count=1,
        )
        db.add(user_rating)
        migrated += 1

    # Update denormalized counters
    user.onboarding_ratings_count = migrated
    if migrated >= 15:
        user.onboarding_completed = True

    await db.commit()

    # Trigger clustering in background if enough ratings (AGENTS.md Background Tasks rule)
    if migrated >= 5:
        qdrant_singleton = qdrant

        async def _run_clustering(uid: int):
            from services.clustering_service import ClusteringService
            async with AsyncSessionLocal() as session:
                try:
                    clustering = ClusteringService(qdrant=qdrant_singleton)
                    await clustering.create_user_clusters(uid, session, groq_client=None)
                except Exception as e:
                    logger.error(f"[migrate-guest] Clustering failed for user {uid}: {e}")

        background_tasks.add_task(_run_clustering, user_id)

    # Invalidate profile cache
    await set_profile_dirty(user_id, REDIS_URL)

    return {"status": "ok", "migrated": migrated}


# ---------------------------------------------------------------------------
# POST /tags — Save tag preferences (Settings UI)
# ---------------------------------------------------------------------------

@router.post("/tags")
async def save_tags(
    body: TagsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user),
):
    """Save content tag preferences. Used by Settings UI."""
    # Validate against whitelist
    unknown_avoided = set(body.avoided) - TAG_WHITELIST
    if unknown_avoided:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown tags: {unknown_avoided}",
        )

    user_result = await db.execute(select(User).where(User.id == current_user.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.tag_preferences = {"avoided": body.avoided}
    await db.commit()

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /status — Onboarding completion status
# ---------------------------------------------------------------------------

@router.get("/status")
async def get_onboarding_status(
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user),
):
    """Return onboarding state for the current user."""
    user_result = await db.execute(select(User).where(User.id == current_user.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "ratings_count": user.onboarding_ratings_count,
        "completed": user.onboarding_completed,
        "tags_set": user.tag_preferences is not None,
        "tag_preferences": user.tag_preferences,
    }
