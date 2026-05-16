"""
Auth router — Clerk-only.
Legacy PIN/cookie endpoints were removed; Clerk handles sign-in, sign-up, and
session lifecycle. `/me` remains to hydrate VectorBox user data after Clerk login.
`/claim-anonymous` promotes a guest cookie session to the authenticated user.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import AsyncSessionLocal, REDIS_URL, get_db, IS_PRODUCTION
from models.database import User, UserRating
from models.schemas import TokenResponse
from dependencies import (
    get_current_user,
    get_anonymous_user,
    get_qdrant_service,
    verify_anon_session,
    ANON_COOKIE_NAME,
)
from services.onboarding_service import maybe_complete_onboarding
from services.qdrant_service import QdrantService

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
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: TokenResponse = Depends(get_current_user),
    qdrant: QdrantService = Depends(get_qdrant_service),
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

    # Copy tag_preferences only if the registered user has none yet.
    # onboarding_completed / onboarding_ratings_count are NOT copied from anon —
    # `maybe_complete_onboarding` below recomputes the count from the post-merge
    # row count, which is the authoritative source after the rating reassignment.
    registered_result = await db.execute(
        select(User).where(User.id == registered_user_id)
    )
    registered_user = registered_result.scalar_one_or_none()
    if (
        registered_user
        and not registered_user.onboarding_completed
        and anon_user.tag_preferences
        and not registered_user.tag_preferences
    ):
        registered_user.tag_preferences = anon_user.tag_preferences

    # Delete the anonymous user (CASCADE will clean up remaining ratings, clusters)
    await db.delete(anon_user)
    await db.flush()  # ensure reassigned ratings + delete are visible to the next query

    # Recompute onboarding counter from the authoritative row count + flip
    # onboarding_completed when the threshold is met. Single source of truth
    # for onboarding promotion (`services/onboarding_service.maybe_complete_onboarding`).
    await maybe_complete_onboarding(registered_user_id, db, commit=False)

    await db.commit()

    # Clear cookie
    _clear_anon_cookie(response)

    # The registered user just inherited ratings; their clusters (probably none —
    # they're freshly created) need to be built so BYW / picked_for_you / cult_actor
    # don't return empty on first feed render. Same threshold the carousel uses.
    if migrated_count >= 5:
        background_tasks.add_task(_run_post_claim_clustering, registered_user_id, qdrant)
    background_tasks.add_task(_invalidate_user_feed_cache, registered_user_id)

    logger.info(
        f"[claim-anonymous] Migrated {migrated_count} ratings from "
        f"anon_user={anon_user_id} to user={registered_user_id}"
    )

    return {
        "status": "ok",
        "migrated": True,
        "ratings_transferred": migrated_count,
    }


async def _run_post_claim_clustering(user_id: int, qdrant: QdrantService) -> None:
    """Build clusters for the user who just claimed an anonymous session.
    Background task — owns its own session, never re-raises (AGENTS.md)."""
    from services.clustering_service import ClusteringService
    async with AsyncSessionLocal() as session:
        try:
            clustering = ClusteringService(qdrant=qdrant)
            await clustering.create_user_clusters(user_id, session, groq_client=None)
        except Exception as e:
            logger.error(f"[claim-anonymous] Clustering failed for user {user_id}: {e}")


async def _invalidate_user_feed_cache(user_id: int) -> None:
    """Sweep section + signal cache keys for the freshly-claimed user so the
    first feed render after migration doesn't serve stale empty sections."""
    import redis.asyncio as aioredis
    from config import FEED_CACHE_VERSION
    from services.cache_service import scan_and_delete

    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            for pattern in (
                f"section:{FEED_CACHE_VERSION}:{user_id}:*",
                f"signal_cache:{user_id}:*",
            ):
                await scan_and_delete(r, pattern)
            await r.delete(f"cluster_rotation:{FEED_CACHE_VERSION}:{user_id}")
        finally:
            await r.close()
    except Exception as e:
        logger.warning(f"[claim-anonymous] Cache invalidation failed for user {user_id}: {e}")


def _clear_anon_cookie(response: Response):
    """Expire the anonymous session cookie. SameSite=Strict matches the
    attributes used when the cookie was originally set in /onboarding/init-session;
    browsers require the clearing cookie to share SameSite/Secure/Path to
    reliably overwrite the original."""
    response.set_cookie(
        key=ANON_COOKIE_NAME,
        value="",
        httponly=True,
        samesite="strict",
        secure=IS_PRODUCTION,
        max_age=0,
        path="/",
    )
