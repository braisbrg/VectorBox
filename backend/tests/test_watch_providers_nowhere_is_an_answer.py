"""«Not on any service, anywhere» is an answer, not a failure (found 2026-11-09).

`TMDBClient.get_watch_providers` returned None both when TMDB did not answer and when it
answered `"results": {}` — a film no service carries in any country. Its callers keep
None as "network failure, do not cache", so those films were asked about again on every
request, forever. That happens in the request path: with the maintenance stopped, 434 of
u212's 606 pending watchlist films had stale availability and every view re-fetched them.

The second test keeps the other half of the contract: a real non-answer is still None.
"""
import pytest

from services.tmdb_client import TMDBClient


class _Redis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, _ttl, value):
        self.store[key] = value


def _client(answer):
    client = TMDBClient.__new__(TMDBClient)  # no HTTP client, no settings
    redis = _Redis()

    async def _get_redis():
        return redis

    async def _make_request(*_args, **_kwargs):
        return answer

    client._get_redis = _get_redis
    client._make_request = _make_request
    return client, redis


@pytest.mark.asyncio
async def test_a_film_on_no_service_anywhere_is_cached_as_empty():
    client, redis = _client({"id": 1, "results": {}})
    assert await client.get_watch_providers(1, "ES") == {}
    assert redis.store, "the answer was not cached, so every request asks TMDB again"


@pytest.mark.asyncio
async def test_no_answer_from_tmdb_is_still_none_and_not_cached():
    client, redis = _client(None)
    assert await client.get_watch_providers(1, "ES") is None
    assert not redis.store
