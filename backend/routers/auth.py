"""
Auth router — Clerk-only.
Legacy PIN/cookie endpoints were removed; Clerk handles sign-in, sign-up, and
session lifecycle. `/me` remains to hydrate VectorBox user data after Clerk login.
`/claim-anonymous` promotes a guest cookie session to the authenticated user.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_db, IS_PRODUCTION
from models.database import User, UserRating
from models.schemas import TokenResponse
from dependencies import (
    get_current_user,
    get_anonymous_user,
    verify_anon_session,
    ANON_COOKIE_NAME,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/logout")
async def logout():
    """No-op retained for transition; Clerk's signOut() invalidates the real session."""
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=TokenResponse)
async def read_users_me(current_user: TokenResponse = Depends(get_current_user)):
    """Return the authenticated user's VectorBox profile."""
    return current_user


@router.post("/claim-anonymous")
async def claim_anonymous(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user),
):
    """
    Promote an anonymous session to the authenticated Clerk user.
    Transfers all ratings from the anonymous user to the authenticated user,
    then deletes the anonymous user and clears the cookie.
    Idempotent: returns success if no anonymous session exists.
    """
    cookie_value = request.cookies.get(ANON_COOKIE_NAME)
    if not cookie_value:
        return {"status": "ok", "migrated": False, "message": "No anonymous session to claim"}

    anon_user_id = verify_anon_session(cookie_value)
    if anon_user_id is None:
        # Expired or invalid cookie — just clear it
        _clear_anon_cookie(response)
        return {"status": "ok", "migrated": False, "message": "Anonymous session expired"}

    # Load anonymous user
    result = await db.execute(
        select(User).where(User.id == anon_user_id, User.is_anonymous.is_(True))
    )
    anon_user = result.scalar_one_or_none()
    if anon_user is None:
        _clear_anon_cookie(response)
        return {"status": "ok", "migrated": False, "message": "Anonymous user not found"}

    registered_user_id = current_user.user_id

    # Don't self-merge
    if anon_user.id == registered_user_id:
        _clear_anon_cookie(response)
        return {"status": "ok", "migrated": False, "message": "Same user"}

    # Transfer ratings: reassign anonymous user's ratings to the registered user.
    # Skip ratings for movies the registered user already rated (no overwrite).
    existing_movie_ids_result = await db.execute(
        select(UserRating.movie_id).where(UserRating.user_id == registered_user_id)
    )
    existing_movie_ids = set(existing_movie_ids_result.scalars().all())

    anon_ratings_result = await db.execute(
        select(UserRating).where(UserRating.user_id == anon_user.id)
    )
    anon_ratings = anon_ratings_result.scalars().all()

    migrated_count = 0
    for rating in anon_ratings:
        if rating.movie_id not in existing_movie_ids:
            rating.user_id = registered_user_id
            migrated_count += 1
        else:
            # Delete duplicate — registered user's rating takes precedence
            await db.delete(rating)

    # Copy onboarding metadata if registered user hasn't onboarded yet
    registered_result = await db.execute(
        select(User).where(User.id == registered_user_id)
    )
    registered_user = registered_result.scalar_one_or_none()
    if registered_user and not registered_user.onboarding_completed:
        if anon_user.onboarding_completed:
            registered_user.onboarding_completed = True
        if anon_user.onboarding_ratings_count > registered_user.onboarding_ratings_count:
            registered_user.onboarding_ratings_count = anon_user.onboarding_ratings_count
        if anon_user.tag_preferences and not registered_user.tag_preferences:
            registered_user.tag_preferences = anon_user.tag_preferences

    # Delete the anonymous user (CASCADE will clean up remaining ratings, clusters)
    await db.delete(anon_user)
    await db.commit()

    # Clear cookie
    _clear_anon_cookie(response)

    logger.info(
        f"[claim-anonymous] Migrated {migrated_count} ratings from "
        f"anon_user={anon_user_id} to user={registered_user_id}"
    )

    return {
        "status": "ok",
        "migrated": True,
        "ratings_transferred": migrated_count,
    }


def _clear_anon_cookie(response: Response):
    """Expire the anonymous session cookie."""
    response.set_cookie(
        key=ANON_COOKIE_NAME,
        value="",
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
        max_age=0,
        path="/",
    )
