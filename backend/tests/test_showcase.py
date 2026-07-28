"""Showcase endpoint — Fase 1 of the landing plan.

The property under test is not "it caches". It is that the landing's default
path has a **closed input set**: an unknown slug can never reach Groq, Qdrant or
Postgres. If a future change makes this endpoint compute on demand, these tests
are what should stop it.
"""
import json

import pytest

from services import showcase_service as sc


def test_slug_set_is_closed():
    for q in sc.SHOWCASE_QUERIES:
        assert sc.is_valid_slug(q["slug"])
    for bogus in ["", "unknown", "grief; DROP TABLE movies", "../etc/passwd", "GRIEF"]:
        assert not sc.is_valid_slug(bogus), f"slug should be rejected: {bogus!r}"


def test_every_query_has_both_locales():
    """The landing ships es/en at parity (455 keys); the showcase must match."""
    for q in sc.SHOWCASE_QUERIES:
        assert q.get("es"), f"{q['slug']} has no Spanish query"
        assert q.get("en"), f"{q['slug']} has no English query"
        assert q["es"] != q["en"], f"{q['slug']} looks untranslated"


def test_query_for_unknown_slug_is_none():
    assert sc.query_for("nope", "es") is None


def test_unknown_language_falls_back_rather_than_failing():
    """A bad ?lang= is a typo, not an attack — serve Spanish instead of 500."""
    assert sc.query_for("grief", "de") == sc.query_for("grief", "es")
    assert sc.cache_key("grief", "de").endswith(":es")


def test_cache_key_is_versioned_independently_of_the_feed():
    """Sharing FEED_CACHE_VERSION would force unrelated invalidations both ways."""
    from config import FEED_CACHE_VERSION
    key = sc.cache_key("grief", "es")
    assert key == f"showcase:{sc.SHOWCASE_VERSION}:grief:es"
    assert FEED_CACHE_VERSION not in key


class _FakeRedis:
    def __init__(self, store=None):
        self.store = store or {}
        self.writes = []

    async def get(self, k):
        return self.store.get(k)

    async def setex(self, k, ttl, v):
        self.writes.append((k, ttl, v))
        self.store[k] = v


@pytest.mark.asyncio
async def test_read_returns_none_for_unknown_slug_without_touching_redis():
    class Exploding:
        async def get(self, k):
            raise AssertionError("unknown slug must not reach Redis")

    assert await sc.read(Exploding(), "nope", "es") is None


@pytest.mark.asyncio
async def test_read_miss_is_none():
    assert await sc.read(_FakeRedis(), "grief", "es") is None


@pytest.mark.asyncio
async def test_round_trip():
    r = _FakeRedis()
    payload = {"slug": "grief", "lang": "es", "query": "q", "results": [{"title": "Drive My Car"}]}
    await sc.write(r, "grief", "es", payload)
    assert await sc.read(r, "grief", "es") == payload
    _, ttl, _ = r.writes[0]
    assert ttl == sc.CACHE_TTL_SECONDS == 60 * 60 * 24 * 7


@pytest.mark.asyncio
async def test_corrupt_entry_is_a_miss_not_a_crash():
    r = _FakeRedis({sc.cache_key("grief", "es"): "{not json"})
    assert await sc.read(r, "grief", "es") is None


def test_service_never_imports_an_engine():
    """The guarantee is structural: this module cannot search even by accident."""
    import inspect
    src = inspect.getsource(sc)
    for forbidden in ["parse_user_intent", "QdrantService", "qdrant", "AsyncSessionLocal", "openai"]:
        assert forbidden not in src, (
            f"showcase_service must stay a cache reader — found {forbidden!r}. "
            "The moment it can compute, the closed-input guarantee is gone."
        )


def test_min_results_sits_between_the_two_observed_cases():
    """Fase 1.5 — the floor is calibrated against real numbers, not taste.

    Above 2: 'with-parents/es' returned 2 films and got cached for a week; that
    row is visibly broken.
    At or below 7: 'heist70/es' returns 7 and that answer is CORRECT — European
    1970s heists is a narrow slice and the catalogue has no more. A floor of 8
    would have thrown away a good result for being specific.
    """
    assert 2 < sc.MIN_RESULTS <= 7


def test_warm_script_enforces_the_two_degradation_rules():
    """These rules live in the script, so guard them there.

    1. below MIN_RESULTS → do not cache at all
    2. a degraded run must never overwrite a healthy cached entry
    """
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("scripts/warm_showcase.py").read_text(encoding="utf-8")
    assert "showcase_service.MIN_RESULTS" in src, "the warm script must enforce the floor"
    assert "existing.get(\"degraded\")" in src, (
        "a degraded run must check for a healthy entry before overwriting it — "
        "otherwise one deploy during a Groq outage downgrades a working page"
    )
