"""What a finished import may remove from a user's library.

A ZIP import prunes the rows the export no longer has, and a full watchlist scrape takes
off the list what Letterboxd no longer lists. Both did it with one rule, "everything this
pass did not touch", which also removed:

  - films the source still names but that found no match THIS time (a blocked Letterboxd
    page, a TMDB tie): one bad lookup deleted a rating the user never touched;
  - in the scrape, films the user added to the watchlist from the app, which Letterboxd
    never had.

Rows written from the app carry watch_count 0 (the web-origin mark) and are never
candidates. Found 2026-11-10.
"""
from sqlalchemy import func, select

from models.database import Movie, UserRating
from services.tmdb_client import _norm_title


async def untouched_rows(db, user_id: int, kept_movie_ids, watchlist_only: bool = False):
    """The user's Letterboxd-origin rows this pass did not touch, with what is needed to
    tell whether the source still names them: (movie_id, title, original_title, year, uri)."""
    stmt = (
        select(UserRating.movie_id, Movie.title, Movie.original_title, Movie.year,
               Movie.letterboxd_uri)
        .join(Movie, Movie.id == UserRating.movie_id)
        .where(UserRating.user_id == user_id)
        .where(UserRating.movie_id.notin_(kept_movie_ids))
        .where(func.coalesce(UserRating.watch_count, 1) != 0)
    )
    if watchlist_only:
        stmt = stmt.where(UserRating.is_watchlist.is_(True))
    return (await db.execute(stmt)).all()


def stale_movie_ids(candidates, unresolved) -> list:
    """The candidates the source really dropped.

    `unresolved`: the source's entries that found no film this time (title, year,
    letterboxd_uri). A candidate one of them names is kept: matched by URI, or by exact
    normalised title with the year within one, the rule the import matcher uses.
    """
    uris = {u["letterboxd_uri"] for u in unresolved if u.get("letterboxd_uri")}
    named = [(_norm_title(u.get("title") or ""), u.get("year")) for u in unresolved]

    def still_named(title, original_title, year, uri) -> bool:
        if uri and uri in uris:
            return True
        own = {_norm_title(t) for t in (title, original_title) if t}
        return any(
            t and t in own and (not y or not year or abs(int(y) - int(year)) <= 1)
            for t, y in named
        )

    return [mid for mid, title, original, year, uri in candidates
            if not still_named(title, original, year, uri)]
