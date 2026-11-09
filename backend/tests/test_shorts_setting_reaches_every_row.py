"""The shorts setting reaches every row of the feed (found 2026-11-09).

`get_main_feed` reads `User.include_shorts` once and hands it to the rows, but three of
them never received it: «Picked For You», «From Your Favorite Directors» and the cult
actor row. They filtered by VBS alone, so they skipped the gate's shorts clause (and
is_excluded / is_adult with it). Measured on the real feed: with shorts OFF, u212's
directors row served Doodlebug (3 min) and Vincent (6 min).

This runs `FeedService.get_main_feed` for real, with stubs only at the frontier (sessions,
cache, row builders), for a user whose setting is True. True is the case that tells:
every builder defaults to False, so a row the value never reaches looks the same as a
correct one when the setting is off.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.feed_service as feed_service
from models.schemas import FeedItem, FeedSection

# Rows whose content depends on the shorts setting. upcoming / leaving_soon / available_now
# are not here: they list unreleased films, expiring catalogue and the user's own watchlist.
ROWS_THAT_FILTER_SHORTS = {
    "popular_letterboxd", "picked_for_you", "because_you_watched", "hidden_gems",
    "niche_picks", "wildcard", "random_picks", "auteur", "cult_actor",
}


class _Session:
    """No database. `scalar` answers the one boolean the feed asks for: include_shorts."""

    async def scalar(self, *_):
        return True

    async def execute(self, *_):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


class _SessionLocal:
    async def __aenter__(self):
        return _Session()

    async def __aexit__(self, *_):
        return False


@pytest.mark.asyncio
async def test_every_row_that_filters_shorts_receives_the_users_setting(monkeypatch):
    received = {}

    def builder(row_id):
        async def build(*_, **kwargs):
            received[row_id] = kwargs.get("include_shorts", "<not passed>")
            return FeedSection(id=row_id, title=row_id, items=[FeedItem(id=1, title=row_id)])
        return build

    class _Recommender:
        def __init__(self, db, **_):
            pass

        get_auteur_section = staticmethod(builder("auteur"))
        get_cult_actor_section = staticmethod(builder("cult_actor"))

    engine = SimpleNamespace(
        _get_anti_vector=AsyncMock(return_value=None),
        get_upcoming_section=builder("upcoming"),
        get_leaving_soon_section=builder("leaving_soon"),
    )
    monkeypatch.setattr(feed_service, "RecommendationEngine", lambda **_: engine)
    monkeypatch.setattr(feed_service, "AsyncSessionLocal", _SessionLocal)
    monkeypatch.setattr(feed_service, "_get_cached_section", AsyncMock(return_value=None))
    monkeypatch.setattr(feed_service, "_cache_section", AsyncMock())
    monkeypatch.setattr(feed_service, "ProviderService", lambda *_: None)
    monkeypatch.setattr(feed_service, "RecommendationService", _Recommender)

    svc = feed_service.FeedService()
    for method, row_id in [
        ("get_popular_on_letterboxd_section", "popular_letterboxd"),
        ("get_hybrid_picks_section", "picked_for_you"),
        ("get_because_you_watched_section", "because_you_watched"),
        ("get_hidden_gems_section", "hidden_gems"),
        ("get_niche_picks_section", "niche_picks"),
        ("get_wildcard_section", "wildcard"),
        ("get_random_recommendations_section", "random_picks"),
        ("get_available_now_section", "available_now"),
    ]:
        setattr(svc, method, builder(row_id))

    await svc.get_main_feed(
        user_id=1, country_code="ES", streaming_providers=[8],
        tmdb=None, qdrant=None, redis_client=object(),
    )

    wrong = {row: received.get(row, "<row not built>") for row in ROWS_THAT_FILTER_SHORTS
             if received.get(row) is not True}
    assert not wrong, f"rows that did not get include_shorts=True: {wrong}"
