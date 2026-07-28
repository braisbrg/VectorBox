"""Showcase — the canned queries the landing page shows to visitors.

Fase 1 of the landing plan. The point is not caching for speed: it is that the
landing must reach a **closed set of inputs**. `/search/natural` accepts free
text from anyone without a session, which its own docstring calls "a paid-LLM
proxy"; the showcase path accepts only a slug from the list below, so there is
no text for a stranger to put in front of Groq.

Two consequences, both deliberate:

* This module NEVER calls Groq, Qdrant or Postgres. It reads Redis and returns.
  A miss is a 503, not a computation — that is what makes the guarantee real
  rather than a promise about rate limits.
* Filling the cache is `scripts/warm_showcase.py`, which drives the ordinary
  `/search/natural` pipeline. One code path produces the results, so the
  showcase can never drift from what Magic Box would have answered.

The curated list lives here rather than in `config.py` following the precedent
of `GLOBAL_THEMES` (recommendation_engine.py): curated *content* sits with the
service that owns it; `config.py` holds versions and wiring.
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Bump to invalidate every cached showcase answer at once. Deliberately NOT
# FEED_CACHE_VERSION: nothing here depends on feed logic, and sharing a version
# forces unrelated invalidations on both sides.
SHOWCASE_VERSION = "v1"

CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days; the catalogue moves slowly

# The landing's example queries. Keep this list SHORT — every entry is a row in
# the cache and a query someone has to sanity-check by eye. Each one must be a
# sentence a real person would type, not a demo of the parser's features.
SHOWCASE_QUERIES: list[dict[str, str]] = [
    {
        "slug": "grief",
        "es": "algo lento y triste sobre el duelo, sin sustos",
        "en": "something slow and sad about grief, no jump scares",
    },
    {
        "slug": "heist70",
        "es": "atracos con mucho estilo, cine europeo de los 70",
        "en": "stylish heists, European cinema of the 70s",
    },
    {
        # Replaced twice, and the second time taught us the rule. Both earlier
        # phrasings asked about AUDIENCE ("that my parents and I would finish",
        # "that parents and kids will both enjoy"), and the catalogue is embedded
        # on what a film is ABOUT. The parser expanded them correctly —
        # "family-friendly, gentle, wholesome, safe for all ages" — but no film's
        # description says it is safe for all ages, so the nearest neighbours were
        # whatever was vaguely animated: Boss Baby, a direct-to-video Charlotte's
        # Web sequel, YES DAY at VBS 44. Mean similarity 44.7.
        #
        # A theme the catalogue can actually answer scores 67.5 and returns Wings
        # of Desire and L'Eclisse. Showcase queries must name a SUBJECT, not an
        # audience — that is what the vector space holds.
        "slug": "loneliness",
        "es": "la soledad de vivir en una ciudad enorme",
        "en": "the loneliness of living in a huge city",
    },
]

# Floor for caching an answer. Calibrated against both failure modes, not picked:
#   · 2 films (the old with-parents/es) is a broken row and must be rejected.
#   · 7 films (heist70/es) is a *correct* answer — "European + 1970s + heists"
#     is genuinely narrow and the catalogue has no more. Rejecting it would
#     punish a good query for being specific.
# So the floor sits between those two observed numbers. A row of 6 does not
# scroll, which is fine; a row of 2 looks broken, which is not.
MIN_RESULTS = 6

# Mean similarity below this means the catalogue cannot answer the question, not
# that the answer is merely narrow. Calibrated on two measured queries: "grief"
# answers at a 67.5 mean (Ordinary People, Petite Maman) and "parents and kids
# will both enjoy" at 44.7 (Boss Baby, a Charlotte's Web sequel, YES DAY at VBS
# 44). The gap is the whole signal, and nothing in the pipeline was reading it.
MIN_MEAN_SCORE = 55

_BY_SLUG = {q["slug"]: q for q in SHOWCASE_QUERIES}
_LANGS = ("es", "en")


def is_valid_slug(slug: str) -> bool:
    """The whole security model in one function: unknown slug, no work done."""
    return slug in _BY_SLUG


def query_for(slug: str, lang: str) -> Optional[str]:
    """The natural-language query behind a slug, or None if the slug is unknown."""
    entry = _BY_SLUG.get(slug)
    if not entry:
        return None
    return entry.get(lang if lang in _LANGS else "es")


def cache_key(slug: str, lang: str) -> str:
    return f"showcase:{SHOWCASE_VERSION}:{slug}:{lang if lang in _LANGS else 'es'}"


async def read(redis, slug: str, lang: str) -> Optional[dict]:
    """Cached answer for a slug, or None on a miss. Never computes."""
    if not is_valid_slug(slug):
        return None
    raw = await redis.get(cache_key(slug, lang))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # A corrupt entry is a miss, not a 500 — the warm script will replace it.
        logger.warning("Corrupt showcase cache entry for %s/%s; treating as miss", slug, lang)
        return None


async def write(redis, slug: str, lang: str, payload: dict) -> None:
    """Store one answer. Called by scripts/warm_showcase.py, not by a request."""
    await redis.setex(cache_key(slug, lang), CACHE_TTL_SECONDS, json.dumps(payload))
