"""The two count checks that guard the watchlist sync, both reading the same free
number: Letterboxd's own `data-num-entries` from page 1.

`_watchlist_settled` — may the scrape STOP at the already-synced boundary, or must
it run the whole list for the removal reconcile? LB-total vs the LB-total we stored
last sync, so our resolvable/irresolvable offset never enters the maths.

`_scrape_was_complete` — did the scrape actually REACH the end? A block the retries
never cleared returns the same empty page as a 404, and the reconcile demotes
everything it didn't see.
"""
from routers.rss import _watchlist_settled, _scrape_was_complete


# --- _watchlist_settled: stop early, or scrape the whole list? -------------

def test_no_change_stops():
    # nothing added or removed → total == baseline → safe to stop
    assert _watchlist_settled(601, 601) is True


def test_pure_additions_stop():
    # 2 added, none removed → total only grew → stop
    assert _watchlist_settled(601, 603) is True


def test_pure_removal_keeps_scraping():
    # 1 removed → total dropped below baseline → full scrape for the reconcile
    assert _watchlist_settled(601, 600) is False


def test_masked_removal_is_the_weekly_nets_job():
    # add 1 + remove 1 keeps the total flat, so this gate cannot see it. Documented
    # limitation, not a bug: the weekly force_reconcile catches it. If this ever has
    # to be caught same-sync, the gate needs the addition count back (and the
    # mid-scrape flag that came with it).
    assert _watchlist_settled(601, 601) is True


def test_no_baseline_keeps_scraping():
    # first sync / Redis-evicted → full scrape to reconcile + re-establish baseline
    assert _watchlist_settled(None, 601) is False


def test_no_total_stops_gracefully():
    # page-1 parse failed → no signal → plain incremental stop, weekly net covers
    assert _watchlist_settled(601, None) is True
    assert _watchlist_settled(None, None) is True


# --- _scrape_was_complete: is the reconcile allowed to fire? ---------------

def test_complete_scrape_reconciles():
    assert _scrape_was_complete(601, 601) is True


def test_truncated_scrape_blocks_the_reconcile():
    # page 7 of 22 hit a 429 the retries never cleared → the loop broke as if the
    # list had ended. Without this the reconcile demotes every row past page 6.
    assert _scrape_was_complete(180, 601) is False


def test_page_one_block_blocks_the_reconcile():
    assert _scrape_was_complete(0, 601) is False


def test_removal_mid_scrape_skips_rather_than_fires():
    # one film left while we were scraping → we come back one short. Skipping this
    # sync's reconcile is free; firing it on a short list is not.
    assert _scrape_was_complete(600, 601) is False


def test_addition_mid_scrape_still_reconciles():
    # `>=`, not `==` — a film added while we scraped must not block the reconcile
    assert _scrape_was_complete(602, 601) is True


def test_no_total_allows_the_reconcile():
    # nothing to check against → fall back to the old behaviour
    assert _scrape_was_complete(601, None) is True


# --- the wiring: does a truncated scrape actually spare the tail? ---------
#
# The predicates above only prove the arithmetic. What loses data is the WIRING:
# lb_rank counting every film, lb_total surviving from page 1, and the reconcile
# reading both. So drive `_run_sync_background` with a scraper that dies mid-list.
#
# PAIRED, and that is the point: `_run_sync_background` swallows every exception
# into a log line, so "no UPDATE was issued" is also what a broken mock produces.
# The complete-scrape case is the control — if the harness rots, it fails first.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class _FakeDB:
    """Records what got executed. First SELECT = the known-watchlist query."""

    def __init__(self):
        self.updates = []
        self._selects = 0

    async def execute(self, stmt):
        from sqlalchemy.sql.dml import Update
        if isinstance(stmt, Update):
            self.updates.append(stmt)
            return MagicMock(rowcount=7)
        self._selects += 1
        if self._selects == 1:
            return MagicMock(all=lambda: [(500,), (501,), (502,), (503,)])
        row = MagicMock(is_watchlist=True, watchlist_rank=0)
        result = MagicMock()
        result.scalars.return_value.first.return_value = row
        return result

    def add(self, obj):
        pass

    async def commit(self):
        pass


def _fake_scraper(pages):
    """pages: {page_no: (films, has_more, total)}. Missing page = blocked/404."""
    scraper = MagicMock()
    scraper._scrape_listing_page = AsyncMock(
        side_effect=lambda _u, _p, page: pages.get(page, ([], False, None))
    )
    scraper.get_tmdb_id = AsyncMock(side_effect=lambda slug: 500 + int(slug.split("-")[1]))
    scraper.set_resolved_tmdb_id = AsyncMock()
    scraper.close = AsyncMock()
    return scraper


def _films(*idx):
    return [{"film_slug": f"film-{i}", "year": 2000, "title": f"Film {i}"} for i in idx]


async def _run(pages, baseline, caplog):
    """Drive the real _run_sync_background over mocked I/O. Returns the FakeDB."""
    from routers import rss

    db = _FakeDB()

    class _CM:
        async def __aenter__(self_): return db
        async def __aexit__(self_, *a): return False

    redis = MagicMock()
    redis.get = AsyncMock(return_value=str(baseline))
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    redis.close = AsyncMock()

    movie_service = MagicMock()
    movie_service.get_or_create_movie = AsyncMock(
        side_effect=lambda tmdb_id, letterboxd_uri: MagicMock(id=tmdb_id)
    )
    movie_service.close = AsyncMock()

    rss_service = MagicMock()
    rss_service.sync_user_rss = AsyncMock()

    with patch("config.AsyncSessionLocal", lambda: _CM()), \
         patch("redis.asyncio.from_url", return_value=redis), \
         patch.object(rss, "ScraperService", return_value=_fake_scraper(pages)), \
         patch.object(rss, "MovieService", return_value=movie_service), \
         patch.object(rss, "RSSService", return_value=rss_service), \
         patch.object(rss, "_invalidate_feed_cache", AsyncMock()):
        await rss._run_sync_background(1, "someone", MagicMock())

    # The harness itself must not be what produced the result.
    assert "Background sync failed" not in caplog.text, caplog.text
    return db


# 4 films over 2 good pages, then the wheels come off on page 3.
_TRUNCATED = {1: (_films(0, 1), True, None), 2: (_films(2, 3), True, None)}


@pytest.mark.asyncio
async def test_truncated_scrape_does_not_demote_the_tail(caplog):
    # Letterboxd says 100 films; we got 4 before page 3 came back empty — which is
    # what a 429 the retries never cleared looks like from here. Demoting now would
    # clear 96 films off the watchlist.
    pages = dict(_TRUNCATED)
    pages[1] = (_films(0, 1), True, 100)
    with caplog.at_level("INFO"):
        db = await _run(pages, baseline=101, caplog=caplog)
    assert db.updates == [], "reconcile fired on a scrape that saw 4 of 100 films"
    assert "came back SHORT" in caplog.text


@pytest.mark.asyncio
async def test_complete_scrape_still_demotes(caplog):
    # CONTROL — same mocks, same pages, only the total changes: 4 of 4 films seen,
    # so page 3's empty response really was the end of the list. If this stops
    # firing, the test above is passing for the wrong reason.
    pages = dict(_TRUNCATED)
    pages[1] = (_films(0, 1), True, 4)
    with caplog.at_level("INFO"):
        db = await _run(pages, baseline=5, caplog=caplog)
    assert len(db.updates) == 1, "reconcile did not fire on a complete scrape"


if __name__ == "__main__":
    for _n, _f in list(globals().items()):
        if _n.startswith("test_") and not _n.startswith("test_truncated") \
                and not _n.startswith("test_complete"):
            _f()
    print("ok (predicates only — run the async pair under pytest)")
