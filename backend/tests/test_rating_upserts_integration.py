"""Every writer of a user's row, executed on Postgres, each with its own rule.

Found 2026-11-10. The statements wrote `excluded.<column>` (or the request's default)
as it came, whatever the source:

  - RSS is the latest activity, so an entry decides rating, review and like when it is
    the most recent viewing we know of. The feed lists newest first, so the OLDER of two
    entries for one film was processed last and won; and an entry with no stars (a
    rewatch logged without re-rating) wiped the stored rating and review.
  - ZIP is the complete snapshot of Letterboxd, so it DOES clear what it leaves empty.
    That is the design, pinned here so nobody "fixes" it: un-rating a film on
    Letterboxd has to reach us.
  - The app's watchlist button sends only `is_watchlist`; `is_liked` defaulted to False,
    so every press erased the film's like. Re-rating stamped now() over a diary date.
  - A film added to the watchlist from the app looked like a Letterboxd row
    (watch_count 1), so the next import or full scrape removed it.

`integration` because the rules live in SQL (COALESCE, GREATEST, the web-origin filter)
and only Postgres can run them. Everything happens in one transaction that is rolled
back: no row survives. One test function on purpose: the engine's pool belongs to the
first event loop.
"""
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select


@pytest.mark.integration
@pytest.mark.asyncio
async def test_each_source_overwrites_by_its_own_rule():
    from config import AsyncSessionLocal
    from models.database import Movie, User, UserRating
    from routers.movies import RateMovieRequest, web_rating_upsert
    from routers.upload import zip_rating_upsert
    from services.import_reconcile import untouched_rows
    from services.rss_service import rss_rating_upsert

    async with AsyncSessionLocal() as s:
        try:
            user = User(username=f"it-upsert-{uuid.uuid4().hex[:12]}")
            s.add(user)
            await s.flush()
            movie_id, other_id = (await s.execute(
                select(Movie.id).order_by(Movie.id).limit(2))).scalars().all()

            async def row():
                return (await s.execute(
                    select(UserRating.rating, UserRating.watched_date, UserRating.review,
                           UserRating.watch_count, UserRating.is_liked, UserRating.is_watchlist)
                    .where(UserRating.user_id == user.id, UserRating.movie_id == movie_id)
                )).one()

            newer, older = datetime(2026, 5, 2), datetime(2024, 1, 1)

            # RSS lists newest first: a rated, dated, reviewed, liked entry...
            await s.execute(rss_rating_upsert(user.id, movie_id, {
                "rating": 4.5, "watched_date": newer, "review": "great", "is_liked": True}, False))
            # ...then an OLDER entry of the same film with other values, processed after it.
            await s.execute(rss_rating_upsert(user.id, movie_id, {
                "rating": 1.0, "watched_date": older, "review": "meh", "is_liked": False}, False))
            r = await row()
            assert (r.rating, r.watched_date, r.review, r.is_liked) == (4.5, newer, "great", True)

            # The latest entry without stars or review erases neither.
            await s.execute(rss_rating_upsert(
                user.id, movie_id, {"rating": None, "watched_date": newer, "review": None,
                                    "is_liked": True}, False))
            r = await row()
            assert (r.rating, r.review) == (4.5, "great")

            # A real new value still wins, and a flagged rewatch still counts.
            latest = datetime(2026, 6, 1)
            await s.execute(rss_rating_upsert(
                user.id, movie_id, {"rating": 3.0, "watched_date": latest}, True))
            r = await row()
            assert (r.rating, r.watched_date, r.watch_count) == (3.0, latest, 2)

            # ZIP: the complete snapshot of Letterboxd, so the opposite rule, by design.
            # Watched, unrated, no diary entry THERE means exactly that HERE: un-rating a
            # film on Letterboxd has to reach us.
            await s.execute(zip_rating_upsert(
                user.id, movie_id, {"is_watched": True, "watch_count": 1}))
            r = await row()
            assert (r.rating, r.watched_date, r.review, r.watch_count) == (None, None, None, 1)

            export_date = datetime(2023, 1, 1)
            await s.execute(zip_rating_upsert(user.id, movie_id, {
                "rating": 5.0, "watched_date": export_date, "is_watched": True, "is_liked": True}))
            r = await row()
            assert (r.rating, r.watched_date, r.is_liked) == (5.0, export_date, True)

            # The app's watchlist button: only the watchlist flag changes.
            await s.execute(web_rating_upsert(user.id, movie_id, RateMovieRequest(is_watchlist=True)))
            r = await row()
            assert (r.is_watchlist, r.is_liked, r.rating, r.watched_date) == (True, True, 5.0, export_date)

            # Re-rating from the app: the diary date stays, and so does the watchlist flag.
            await s.execute(web_rating_upsert(user.id, movie_id, RateMovieRequest(rating=2.0)))
            r = await row()
            assert (r.rating, r.watched_date, r.is_watchlist, r.is_liked) == (2.0, export_date, True, True)

            # A film added to the watchlist from the app is web-origin: an import that
            # touched nothing may remove the Letterboxd row, never this one.
            await s.execute(web_rating_upsert(user.id, other_id, RateMovieRequest(is_watchlist=True)))
            candidates = {c[0] for c in await untouched_rows(s, user.id, [], watchlist_only=True)}
            assert candidates == {movie_id}
        finally:
            await s.rollback()
