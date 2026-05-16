"""
User management router
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
import logging

from config import get_db
from dependencies import get_current_user, get_http_client, verify_user_ownership
from models.database import User
from models.schemas import UserResponse, TokenResponse, LinkLetterboxdRequest

logger = logging.getLogger(__name__)
router = APIRouter()

# Letterboxd usernames are 2–15 chars, lowercase letters/numbers/underscore.
# Source: signup form rejects anything else. Validate strictly so we don't
# end up making requests for `..`, `foo/bar`, `?q=`, or other path-injection
# shapes — bounded to letterboxd.com so not SSRF, but still wrong-looking.
LETTERBOXD_USERNAME_RE = re.compile(r"^[a-z0-9_]{2,15}$")


# M-1: Legacy POST /api/users removed. Use POST /api/auth/register instead.


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
