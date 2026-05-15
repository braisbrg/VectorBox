"""Centralised `onboarding_completed` maintenance.

Before this helper existed, only the carousel `/rate` endpoint and the
`/migrate-guest` flow flipped the flag — ZIP uploads, RSS syncs, and the
`/movies/{id}/rate` route added thousands of ratings without ever updating
`onboarding_completed` / `onboarding_ratings_count`. Users showed up in the
DB with hundreds of ratings but the frontend still treated them as "in
onboarding" (B-20 + F-37).

Call `maybe_complete_onboarding` after any path that adds UserRating rows.
The function is idempotent and self-throttling — it short-circuits if the
flag is already True, and recomputes the denormalised counter from the
authoritative `user_ratings` row count so it can't drift.
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import User, UserRating

# Same threshold used by the carousel `/rate` endpoint (routers/onboarding.py:517).
# Centralising it here so any future tuning is one-line change.
ONBOARDING_THRESHOLD = 15


async def maybe_complete_onboarding(
    user_id: int,
    db: AsyncSession,
    *,
    threshold: int = ONBOARDING_THRESHOLD,
    commit: bool = True,
) -> bool:
    """Promote a user out of onboarding when they have enough ratings.

    Refreshes `onboarding_ratings_count` from the authoritative row count and
    flips `onboarding_completed=True` once the threshold is met. No-op when
    the flag is already True.

    Returns True if the flag was just flipped, False otherwise.

    `commit=False` lets the caller batch this into an existing transaction
    (RSS sync / ZIP upload both already manage their own commit).
    """
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or user.onboarding_completed:
        return False

    count = await db.scalar(
        select(func.count(UserRating.id)).where(UserRating.user_id == user_id)
    )
    count = count or 0

    user.onboarding_ratings_count = count
    flipped = False
    if count >= threshold:
        user.onboarding_completed = True
        flipped = True

    if commit:
        await db.commit()

    return flipped
