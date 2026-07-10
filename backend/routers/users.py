"""
User management router
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, delete, or_
from sqlalchemy.exc import IntegrityError
import logging

from config import get_db, REDIS_URL
from dependencies import get_current_user, get_http_client, verify_user_ownership
from models.database import User, UserRating, Movie, UserCluster
from models.schemas import UserResponse, TokenResponse, LinkLetterboxdRequest

logger = logging.getLogger(__name__)
router = APIRouter()

# Letterboxd usernames are 2–15 chars, lowercase letters/numbers/underscore.
# Source: signup form rejects anything else. Validate strictly so we don't
# end up making requests for `..`, `foo/bar`, `?q=`, or other path-injection
# shapes — bounded to letterboxd.com so not SSRF, but still wrong-looking.
LETTERBOXD_USERNAME_RE = re.compile(r"^[a-z0-9_]{2,15}$")


# M-1: Legacy POST /api/users removed. Use POST /api/auth/register instead.


@router.get("/me/profile")
async def get_my_profile(
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregates for the ACID /you profile hub (H2 split dossier).

    All values derive from persisted data — no LLM calls on this path:
      - stats: rated count, cluster count, avg ★, day streak, this-month count
      - signature: the Redis-cached LLM profile summary (null if not generated)
      - tier: presentation heuristic over library size
      - trident: {vibe, auteur, gems} — auteur = share of 4★+ films whose
        director repeats, gems = share of low-vote-count films, vibe = rest
      - activity: films per month, last 12 months (sparkline)
      - top_clusters + recently_rated strips
    """
    from datetime import datetime, timedelta, timezone

    user_id = current_user.user_id
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Core stats in one aggregate.
    stats_row = (
        await db.execute(
            select(
                func.count(),
                func.avg(UserRating.rating),
            ).where(UserRating.user_id == user_id, UserRating.rating.isnot(None))
        )
    ).first()
    films = int(stats_row[0] or 0)
    avg_rating = float(stats_row[1]) if stats_row[1] is not None else None

    clusters = (
        await db.execute(
            select(UserCluster)
            .where(UserCluster.user_id == user_id)
            .order_by(desc(UserCluster.movie_count))
        )
    ).scalars().all()

    # Watched dates → streak + monthly sparkline (walk in Python; a library is
    # a few thousand rows at most).
    dates = (
        await db.execute(
            select(UserRating.watched_date)
            .where(UserRating.user_id == user_id, UserRating.watched_date.isnot(None))
            .order_by(desc(UserRating.watched_date))
        )
    ).scalars().all()
    day_set = {d.date() for d in dates}
    today = datetime.now(timezone.utc).date()
    streak = 0
    cursor = today if today in day_set else today - timedelta(days=1)
    while cursor in day_set:
        streak += 1
        cursor -= timedelta(days=1)

    sparkline = []
    month_counts: dict = {}
    for d in dates:
        key = (d.year, d.month)
        month_counts[key] = month_counts.get(key, 0) + 1
    mcursor = today.replace(day=1)
    for _ in range(12):
        sparkline.append(month_counts.get((mcursor.year, mcursor.month), 0))
        mcursor = (mcursor - timedelta(days=1)).replace(day=1)
    sparkline.reverse()
    this_month = sparkline[-1] if sparkline else 0

    # Trident split from persisted signals (presentation heuristic, sums to 1).
    # Same pass also feeds the taste-card badge sources (top director/actor/genres).
    rated_movies = (
        await db.execute(
            select(UserRating.rating, Movie.directors, Movie.cast, Movie.genres,
                   Movie.imdb_vote_count, Movie.vote_count)
            .join(Movie, Movie.id == UserRating.movie_id)
            .where(UserRating.user_id == user_id, UserRating.rating.isnot(None))
        )
    ).all()
    director_counts: dict = {}
    director_ratings: dict = {}   # name → [ratings], for "highest rated director"
    actor_ratings: dict = {}      # name → [ratings]
    genre_counts: dict = {}       # genre → count among loved films
    gems = 0
    for rating, directors, cast, genres, imdb_votes, tmdb_votes in rated_movies:
        if (rating or 0) >= 4.0:
            for d in directors or []:
                director_counts[d] = director_counts.get(d, 0) + 1
        for d in directors or []:
            director_ratings.setdefault(d, []).append(rating or 0)
        for a in cast or []:
            actor_ratings.setdefault(a, []).append(rating or 0)
        if (rating or 0) >= 3.5:
            for g in genres or []:
                genre_counts[g] = genre_counts.get(g, 0) + 1
        if (imdb_votes or tmdb_votes or 0) < 20000:
            gems += 1

    # Badge sources: the name whose films you rate highest, with Bayesian shrinkage
    # toward your global mean (same idea as VBS) so a 2-film co-star can't outrank a
    # genuine favourite. Without it, Anne Hathaway (Interstellar 5★ + Alice 4.5★, 2
    # films) beat Nolan (7 films @ 4.5) for braisbg — she's incidental to why those
    # films are loved. Actors need a higher floor (≥3) than directors (≥2): an actor
    # is far more incidental to a film's appeal than its director. Genres by frequency.
    _gmean = avg_rating if avg_rating is not None else 3.5
    def _top_shrunk(m: dict, min_films: int, prior: float = 4.0):
        best, best_score = None, -1.0
        for k, ratings in m.items():
            if len(ratings) < min_films:
                continue
            avg = sum(ratings) / len(ratings)
            adj = (len(ratings) * avg + prior * _gmean) / (len(ratings) + prior)
            if adj > best_score:
                best, best_score = k, adj
        return best
    top_director = _top_shrunk(director_ratings, 2)
    top_actor = _top_shrunk(actor_ratings, 3)
    top_genres = sorted(genre_counts, key=lambda g: genre_counts[g], reverse=True)[:3]
    repeat_auteur_films = sum(c for c in director_counts.values() if c >= 2)
    auteur_raw = min(1.0, repeat_auteur_films / max(films, 1) * 3)
    gems_raw = gems / max(films, 1)
    vibe_raw = max(0.2, 1.0 - 0.5 * (auteur_raw + gems_raw))
    total = vibe_raw + auteur_raw + gems_raw
    trident = {
        "vibe": round(vibe_raw / total, 2),
        "auteur": round(auteur_raw / total, 2),
        "gems": round(gems_raw / total, 2),
    }

    tier_n = 1 + (films >= 100) + (films >= 500) + (films >= 1000)
    tier = f"CINEPHILE · TIER {'I' * tier_n if tier_n <= 3 else 'IV'}"

    recent = (
        await db.execute(
            select(UserRating, Movie)
            .join(Movie, Movie.id == UserRating.movie_id)
            .where(UserRating.user_id == user_id, UserRating.rating.isnot(None))
            .order_by(desc(UserRating.created_at))
            .limit(6)
        )
    ).all()

    # Defining films for the taste card — your genuinely loved films (≥4★ or liked),
    # highest first, not merely the most recently rated. Posters need a valid path.
    defining = (
        await db.execute(
            select(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                Movie.poster_path.isnot(None),
                or_(UserRating.rating >= 4.0, UserRating.is_liked.is_(True)),
            )
            .order_by(desc(UserRating.rating), desc(UserRating.created_at))
            .limit(6)
        )
    ).scalars().all()

    # LLM profile summary — cached only, never generated inline.
    signature = None
    try:
        from services.profile_cache import get_cached_profile_summary
        signature = await get_cached_profile_summary(user_id, REDIS_URL)
    except Exception as e:
        logger.warning(f"Profile summary fetch failed for user {user_id}: {e}")

    return {
        "username": user.username,
        "letterboxd_username": user.letterboxd_username,
        "member_since": user.created_at.strftime("%b %Y").lower() if user.created_at else None,
        "tier": tier,
        "signature": signature,
        "stats": {
            "films": films,
            "clusters": len(clusters),
            "avg_rating": round(avg_rating, 1) if avg_rating is not None else None,
            "streak_days": streak,
            "this_month": this_month,
        },
        "trident": trident,
        "activity_sparkline": sparkline,
        "top_clusters": [
            {"id": c.cluster_id, "name": c.cluster_label, "films": c.movie_count}
            for c in clusters[:5]
        ],
        # Optional taste-card badge sources (real data; user toggles which to show).
        "top_director": top_director,
        "top_actor": top_actor,
        "top_genres": top_genres,
        # Taste-card "defining films" — your loved films (≥4★/liked), highest first.
        "defining_films": [
            {"tmdb_id": m.tmdb_id, "title": m.title, "year": m.year, "poster_url": m.poster_path}
            for m in defining
        ],
        "recently_rated": [
            {
                "tmdb_id": m.tmdb_id,
                "title": m.title,
                "year": m.year,
                "poster_url": m.poster_path,
                "rating": r.rating,
                "q": m.vectorbox_score,
            }
            for r, m in recent
        ],
    }


@router.delete("/me/ratings/{tmdb_id}")
async def delete_my_rating(
    tmdb_id: int,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Un-rate a film — removes the signed-in user's rating for it (profile
    'recently rated' → ✕). Idempotent: a no-op if there was no rating."""
    movie = (await db.execute(select(Movie).where(Movie.tmdb_id == tmdb_id))).scalar_one_or_none()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")
    await db.execute(
        delete(UserRating).where(
            UserRating.user_id == current_user.user_id,
            UserRating.movie_id == movie.id,
        )
    )
    await db.commit()
    return {"status": "ok", "tmdb_id": tmdb_id}


@router.get("", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user),
):
    """
    Return ONLY the calling user's profile.

    Historically this endpoint listed every user on the platform with their
    has_data flag. Under Clerk auth there is no legitimate reason for the
    frontend to know about other users — the legacy "select session user"
    flow died with the multi-user-on-one-machine cookie model. The remaining
    frontend code path (upload-zone activeUserProfile lookup) only ever
    looks up its own ID, so we return a single-element list for shape
    compatibility.
    """
    from sqlalchemy import func
    from models.database import UserRating

    user_result = await db.execute(
        select(User).where(User.id == current_user.user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        return []

    rating_count = await db.scalar(
        select(func.count(UserRating.id)).where(UserRating.user_id == user.id)
    )
    return [{
        "id": user.id,
        "username": user.username,
        "country_code": user.country_code,
        "created_at": user.created_at,
        "has_data": (rating_count or 0) > 0,
        "letterboxd_username": user.letterboxd_username,
    }]


@router.patch("/{user_id}/link-letterboxd")
async def link_letterboxd(
    user_id: int,
    body: LinkLetterboxdRequest,
    request: Request,
    current_user: TokenResponse = Depends(verify_user_ownership),
    db: AsyncSession = Depends(get_db),
):
    """
    Link a Letterboxd profile to a VectorBox user.
    L-3: Username moved from query param to request body for privacy.
    """
    letterboxd_username = (body.letterboxd_username or "").strip().lower()

    # Strict format validation BEFORE we even touch the network. Letterboxd
    # usernames are [a-z0-9_]{2,15}; anything else is either invalid or a
    # path-injection attempt (e.g. `foo/admin`, `..`, `bar?x=1`).
    if not LETTERBOXD_USERNAME_RE.match(letterboxd_username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Letterboxd username — must be 2–15 lowercase letters, digits, or underscores.",
        )

    # Find user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Quick liveness check — re-uses the lifespan singleton AsyncClient
    # instead of building a fresh client per request (anti-pattern from
    # STACK_RULES.md, and adds ~20ms TLS handshake per call).
    http = await get_http_client(request)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        lb_response = await http.get(
            f"https://letterboxd.com/{letterboxd_username}/",
            headers=headers,
            follow_redirects=True,
            timeout=5.0,
        )
        if lb_response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Letterboxd profile '{letterboxd_username}' not found",
            )
    except HTTPException:
        raise
    except Exception as e:
        # Soft-allow on network blips — Letterboxd outage shouldn't block
        # legitimate linking. The format-regex above is the security gate.
        logger.warning(f"Could not validate Letterboxd profile: {e}")

    user.letterboxd_username = letterboxd_username
    await db.commit()

    logger.info(f"User {user.username} linked Letterboxd profile: {letterboxd_username}")

    return {
        "message": "Letterboxd profile linked successfully",
        "user_id": user.id,
        "username": user.username,
        "letterboxd_username": letterboxd_username,
    }


@router.get("/{username}/activity")
async def get_user_activity(
    username: str,
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user)
):
    """
    Get user's last watched and last rated movies.
    H-1: Only the profile owner can access their activity.
    """
    from models.database import UserRating, Movie
    
    # H-1: Ownership check — users can only view their own activity
    if username != current_user.username:
        raise HTTPException(status_code=403, detail="Access denied: cannot view another user's activity")
    
    # Get User ID
    stmt = select(User).where(User.username == username)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # Last Watched
    watched_stmt = select(Movie).join(UserRating).where(
        UserRating.user_id == user.id,
        UserRating.is_watched.is_(True)
    ).order_by(UserRating.watched_date.desc()).limit(1)
    
    watched_result = await db.execute(watched_stmt)
    last_watched = watched_result.scalar_one_or_none()
    
    # Last Rated (Explicit rating > 0)
    rated_stmt = select(Movie).join(UserRating).where(
        UserRating.user_id == user.id,
        UserRating.rating.isnot(None),
        UserRating.rating > 0
    ).order_by(UserRating.watched_date.desc()).limit(1)
    
    rated_result = await db.execute(rated_stmt)
    last_rated = rated_result.scalar_one_or_none()
    
    return {
        "last_watched": {
            "title": last_watched.title,
            "year": last_watched.year,
            "poster_path": last_watched.poster_path
        } if last_watched else None,
        "last_rated": {
            "title": last_rated.title,
            "year": last_rated.year,
            "poster_path": last_rated.poster_path
        } if last_rated else None
    }
