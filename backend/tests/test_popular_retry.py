"""Phase 8 has no fallback source any more, so the retry IS the resilience.

Trakt answered 403 to every key since 2026-08-06 and was deleted 2026-08-18; before
that a single Cloudflare 403 on the warm-up returned [] and the phase "succeeded"
with 0 items and `source=trakt`. The two things that must not silently regress:
the scrape tries more than once, and it gives up after a bounded number of tries
instead of hammering a rate limiter that every attempt re-arms.
"""
import asyncio

import pytest

from services.scraper_service import ScraperService

# One li.posteritem is enough — this test is about the retry, not the parser.
HTML = (
    '<li class="posteritem" data-average-rating="4.07">'
    '<div class="react-component" data-item-slug="parasite-2019" '
    'data-item-name="Parasite (2019)"></div></li>'
)


@pytest.fixture
def no_sleep(monkeypatch):
    """The real loop waits 120s between attempts; the test must not."""
    async def _instant(_seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", _instant)


@pytest.mark.asyncio
async def test_retries_past_a_transient_403(no_sleep, monkeypatch):
    attempts = []

    async def flaky(self):
        attempts.append(1)
        return HTML if len(attempts) == 2 else None  # 403, then 200

    monkeypatch.setattr(ScraperService, "_fetch_popular_html", flaky)
    films = await ScraperService().scrape_popular_this_week()

    assert len(attempts) == 2, "gave up on the first 403 — the phase would empty"
    assert [f["letterboxd_slug"] for f in films] == ["parasite-2019"]


@pytest.mark.asyncio
async def test_gives_up_bounded(no_sleep, monkeypatch):
    attempts = []

    async def always_403(self):
        attempts.append(1)
        return None

    monkeypatch.setattr(ScraperService, "_fetch_popular_html", always_403)
    films = await ScraperService().scrape_popular_this_week()

    assert films == []
    assert len(attempts) == 3, "attempt count changed — each one re-arms the limiter"
