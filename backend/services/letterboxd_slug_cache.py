"""Redis cache for Letterboxd slug -> TMDB ID resolution.

Letterboxd slugs are effectively immutable (the platform never silently
renames them), so once we have a slug->tmdb_id mapping we can keep it for a
long time. The cache eliminates two distinct round-trips:

    1. The Letterboxd film-page scrape used by `ScraperService.get_tmdb_id`.
    2. The TMDB `/search/movie` call used by the fuzzy-fallback path in
       `routers/rss.py` when the film page is unreachable.

Cache key:   letterboxd:slug2tmdb:{slug}
Value:       "{tmdb_id}"  (positive hit)  |  "MISS"  (negative hit)
TTL:         30d positive  |  7d negative

Negative TTL is shorter because Letterboxd or TMDB might gain coverage of
a film that wasn't matchable last time we asked.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional, Union

logger = logging.getLogger(__name__)

_POSITIVE_TTL_S = 30 * 24 * 60 * 60   # 30 days
_NEGATIVE_TTL_S = 7 * 24 * 60 * 60    # 7 days
_MISS_SENTINEL = "MISS"


def _key(slug: str) -> str:
    return f"letterboxd:slug2tmdb:{slug}"


# Three-state return:
#   int   -> cached positive hit
#   "MISS" -> cached negative hit (do not retry)
#   None  -> not in cache, caller should resolve
CacheResult = Union[int, Literal["MISS"], None]


async def get_cached_tmdb_id(slug: str, redis) -> CacheResult:
    """Look up a slug. Returns int (hit), "MISS" (known unresolvable), or
    None (not cached — caller should attempt resolution)."""
    if not slug:
        return None
    try:
        raw = await redis.get(_key(slug))
    except Exception as e:
        logger.warning(f"slug cache GET failed for {slug}: {e}")
        return None
    if raw is None:
        return None
    if raw == _MISS_SENTINEL:
        return _MISS_SENTINEL
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def set_cached_tmdb_id(slug: str, tmdb_id: Optional[int], redis) -> None:
    """Store a slug resolution. Pass `tmdb_id=None` to record a negative hit."""
    if not slug:
        return
    try:
        if tmdb_id is None:
            await redis.set(_key(slug), _MISS_SENTINEL, ex=_NEGATIVE_TTL_S)
        else:
            await redis.set(_key(slug), str(int(tmdb_id)), ex=_POSITIVE_TTL_S)
    except Exception as e:
        logger.warning(f"slug cache SET failed for {slug}: {e}")
