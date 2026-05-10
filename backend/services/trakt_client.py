"""Lightweight Trakt API client for the Signal C "Crowd" data source.

Only public read endpoints are used:
  - /search/tmdb/{tmdb_id}?type=movie  → resolve our TMDB id to a Trakt slug
  - /movies/{slug}/related              → behavioural collab filter

No OAuth needed for these. Authentication is just the Client ID in a header.

Trakt's collab filter consistently outperforms TMDB's /recommendations on
canonical, art-house, and mainstream films (see scripts/experiment_trakt.py
for the comparison). For ultra-niche / very recent films Trakt may return
nothing — we treat that as "no signal", strictly better than TMDB's noise.
"""
import os
import asyncio
import logging
from datetime import timedelta
from typing import Optional, List, Dict, Any

import httpx
import orjson
import redis.asyncio as redis

logger = logging.getLogger(__name__)

TRAKT_BASE = "https://api.trakt.tv"
RELATED_LIMIT = 10
CACHE_TTL_RELATED = timedelta(days=7)  # related films are stable
CACHE_TTL_LOOKUP = timedelta(days=30)  # tmdb→trakt mapping is permanent


class TraktClient:
    """httpx-based Trakt client. Caches /related and /search results in Redis."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        redis_url: Optional[str] = None,
    ):
        self.client_id = client_id or os.getenv("TRAKT_CLIENT_ID")
        self._external_client = client is not None
        self.client = client or httpx.AsyncClient(timeout=15.0)
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379")
        self.redis_client: Optional[redis.Redis] = None

    @property
    def enabled(self) -> bool:
        return bool(self.client_id)

    async def aclose(self):
        if not self._external_client and self.client:
            await self.client.aclose()

    async def _get_redis(self) -> redis.Redis:
        if not self.redis_client:
            self.redis_client = redis.from_url(
                self.redis_url, encoding="utf-8", decode_responses=False
            )
        return self.redis_client

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id or "",
        }

    async def lookup_by_tmdb(self, tmdb_id: int) -> Optional[Dict[str, Any]]:
        """Resolve a TMDB movie id to a Trakt entity. Returns the inner 'movie' dict."""
        if not self.enabled or not tmdb_id:
            return None
        cache_key = f"trakt:lookup:{tmdb_id}"
        r = await self._get_redis()
        try:
            cached = await r.get(cache_key)
            if cached:
                payload = orjson.loads(cached)
                return payload if payload else None
        except Exception as e:
            logger.debug(f"Trakt lookup cache read failed: {e}")

        try:
            resp = await self.client.get(
                f"{TRAKT_BASE}/search/tmdb/{tmdb_id}",
                params={"type": "movie"},
                headers=self._headers(),
            )
        except Exception as e:
            logger.warning(f"Trakt lookup failed for tmdb={tmdb_id}: {e}")
            return None

        if resp.status_code != 200:
            logger.warning(f"Trakt lookup HTTP {resp.status_code} for tmdb={tmdb_id}")
            return None

        results = resp.json() or []
        movie = results[0]["movie"] if results and "movie" in results[0] else None

        # Cache the resolution (even if None — negative cache shorter)
        try:
            ttl = CACHE_TTL_LOOKUP if movie else timedelta(hours=24)
            await r.setex(cache_key, ttl, orjson.dumps(movie or {}))
        except Exception:
            pass

        return movie

    async def related_by_tmdb(self, tmdb_id: int, limit: int = RELATED_LIMIT) -> List[Dict[str, Any]]:
        """Returns the list of related Trakt movies (raw dicts with `ids.tmdb` etc.)."""
        if not self.enabled or not tmdb_id:
            return []

        cache_key = f"trakt:related:{tmdb_id}:{limit}"
        r = await self._get_redis()
        try:
            cached = await r.get(cache_key)
            if cached:
                return orjson.loads(cached)
        except Exception:
            pass

        movie = await self.lookup_by_tmdb(tmdb_id)
        if not movie:
            try:
                await r.setex(cache_key, timedelta(hours=24), orjson.dumps([]))
            except Exception:
                pass
            return []

        slug = movie.get("ids", {}).get("slug") or movie.get("ids", {}).get("trakt")
        if not slug:
            return []

        try:
            resp = await self.client.get(
                f"{TRAKT_BASE}/movies/{slug}/related",
                params={"limit": limit},
                headers=self._headers(),
            )
        except Exception as e:
            logger.warning(f"Trakt /related failed for {slug} (tmdb={tmdb_id}): {e}")
            return []

        if resp.status_code != 200:
            logger.warning(f"Trakt /related HTTP {resp.status_code} for {slug}")
            return []

        related = resp.json() or []
        try:
            await r.setex(cache_key, CACHE_TTL_RELATED, orjson.dumps(related))
        except Exception:
            pass
        return related


_singleton: Optional[TraktClient] = None


def get_trakt_client() -> TraktClient:
    """Singleton accessor — same pattern as TMDBClient via dependencies.py."""
    global _singleton
    if _singleton is None:
        _singleton = TraktClient()
    return _singleton
