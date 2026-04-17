"""
User management router
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
import logging

from config import get_db
from dependencies import get_current_user, verify_user_ownership
from models.database import User
from models.schemas import UserResponse, TokenResponse, LinkLetterboxdRequest

logger = logging.getLogger(__name__)
router = APIRouter()


# M-1: Legacy POST /api/users removed. Use POST /api/auth/register instead.


@router.get("", response_model=list[UserResponse])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user)
):
    """
    List all users with data status
    """
    # Efficiently check if users have ratings
    from sqlalchemy import func
    from models.database import UserRating
    
    # Query users with a count of their ratings
    stmt = (
        select(User, func.count(UserRating.id).label("rating_count"))
        .outerjoin(UserRating)
        .group_by(User.id)
        .offset(skip)
        .limit(limit)
    )
    
    result = await db.execute(stmt)
    users_with_counts = result.all()
    
    # Transform to response model
    response = []
    for user, count in users_with_counts:
        # M-2: letterboxd_username stripped from public listing to prevent enumeration
        user_dict = {
            "id": user.id,
            "username": user.username,
            "country_code": user.country_code,
            "created_at": user.created_at,
            "has_data": count > 0
        }
        response.append(user_dict)
        
    return response


@router.patch("/{user_id}/link-letterboxd")
async def link_letterboxd(
    user_id: int,
    body: LinkLetterboxdRequest,
    current_user: TokenResponse = Depends(verify_user_ownership),
    db: AsyncSession = Depends(get_db)
):
    """
    Link a Letterboxd profile to a VectorBox user.
    L-3: Username moved from query param to request body for privacy.
    """
    letterboxd_username = body.letterboxd_username
    # Find user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Optional: Quick check that Letterboxd profile exists
    # Optional: Quick check that Letterboxd profile exists
    import httpx
    # Use a browser-like User-Agent to avoid superficial blocking
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            lb_response = await client.get(
                f"https://letterboxd.com/{letterboxd_username}/",
                timeout=5.0
            )
            if lb_response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Letterboxd profile '{letterboxd_username}' not found"
                )
        except httpx.TimeoutException:
            logger.warning(f"Timeout validating Letterboxd profile: {letterboxd_username}")
            # Allow linking anyway if network times out
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Could not validate Letterboxd profile: {e}")
            # Allow linking anyway on network errors
    
    user.letterboxd_username = letterboxd_username
    await db.commit()
    
    logger.info(f"User {user.username} linked Letterboxd profile: {letterboxd_username}")
    
    return {
        "message": "Letterboxd profile linked successfully",
        "user_id": user.id,
        "username": user.username,
        "letterboxd_username": letterboxd_username
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
