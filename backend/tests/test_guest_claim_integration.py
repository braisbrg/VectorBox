"""Signing in keeps everything the guest did (found 2026-11-10).

When a guest signs in, their rows move to the account. A film BOTH had used to keep the
account's row and delete the guest's whole, so a guest who rated or rejected a film the
account had only on its watchlist lost that on signing in. Now the rows merge: what the
account already says stays, the guest fills what it lacks.

`integration`: the merge is an UPDATE ... FROM that only Postgres runs. Everything happens
in one transaction that is rolled back, and the engine's pool is disposed at the end so
the next test's event loop gets fresh connections.
"""
import uuid

import pytest
from sqlalchemy import select


@pytest.mark.integration
@pytest.mark.asyncio
async def test_signing_in_merges_the_guests_rows_into_the_account():
    from config import AsyncSessionLocal, engine
    from models.database import Movie, User, UserRating
    from routers.auth import move_guest_rows

    try:
        async with AsyncSessionLocal() as s:
            try:
                account = User(username=f"it-acc-{uuid.uuid4().hex[:12]}")
                guest = User(username=f"it-guest-{uuid.uuid4().hex[:12]}", is_anonymous=True)
                s.add_all([account, guest])
                await s.flush()
                both, guest_only = (await s.execute(
                    select(Movie.id).order_by(Movie.id).limit(2))).scalars().all()

                s.add_all([
                    UserRating(user_id=account.id, movie_id=both, is_watchlist=True,
                               is_watched=False, is_liked=False, is_rejected=False),
                    UserRating(user_id=guest.id, movie_id=both, rating=4.0, is_watched=True,
                               is_watchlist=False, is_liked=False, is_rejected=False),
                    UserRating(user_id=guest.id, movie_id=guest_only, is_rejected=True,
                               is_watched=False, is_watchlist=False, is_liked=False),
                ])
                await s.flush()

                moved = await move_guest_rows(s, guest.id, account.id)

                rows = {r.movie_id: r for r in (await s.execute(
                    select(UserRating).where(UserRating.user_id == account.id)
                    .execution_options(populate_existing=True))).scalars().all()}
                left = (await s.execute(
                    select(UserRating).where(UserRating.user_id == guest.id))).scalars().all()

                assert moved == 1 and left == []
                merged = rows[both]
                assert (merged.rating, merged.is_watched, merged.is_watchlist) == (4.0, True, True)
                assert rows[guest_only].is_rejected is True
            finally:
                await s.rollback()
    finally:
        await engine.dispose()
