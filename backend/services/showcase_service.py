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
        # "europeo" was the whole problem, and the vector was never at fault.
        # Measured 2026-07-30 on the same embedding, one constraint at a time:
        #
        #   ninguno          0.595   Ocean's Eleven, Thomas Crown, Danger: Diabolik
        #   solo 1970-79     0.533   The Sting, The Hot Rock, Family Plot
        #   solo Europa      0.583   Danger: Diabolik, The Heist of the Century
        #   ambos            0.495   Diamonds Are Forever, Pink Panther, Moonraker
        #
        # Each constraint alone is answered well; their AND is answered by
        # nothing. The catalogue holds 5 European 1970s films with any theft
        # keyword at all, so the twenty nearest neighbours inside that box are
        # simply the most 70s-Euro-crime-ish things in it — a giallo, an Omen, a
        # Buñuel — and the row shipped 2 real heists out of 20.
        #
        # The lesson is not "name a subject" (this one did) but: a showcase query
        # must not AND two narrow constraints. Every filter multiplies into the
        # same small catalogue, and a filtered search always returns its nearest
        # neighbours however far away they are.
        "slug": "heist70",
        "es": "atracos con mucho estilo, cine de los 70",
        "en": "stylish heists, cinema of the 70s",
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

# NO reads raw confidence, and the obvious fix does not work — measured
# 2026-07-30 before writing it. The score below is the BLENDED one (cosine ×
# the VBS sigmoid), so a shelf of acclaimed non-answers passes it: heist70's
# broken row averaged 69.5 against a floor of 55. Swapping it for the engine's
# honest signal (search_confidence, mean raw cosine of the top ten) does not
# separate the cases either:
#
#   giallo italiano      0.643      loneliness (fila buena)  0.524
#   grief (fila buena)   0.614      heist70 (fila MALA)      0.478
#   audiencia INRESPONDIBLE 0.551   <- above a row we ship
#
# The unanswerable audience query outscores a good row, exactly as
# nlp_search.py:423 documents ("not weakly right, confidently wrong"). No
# threshold orders these correctly, so none is added: the checks below catch
# gross breakage (too few films, a failed parse) and a human still has to look
# at the row. A real bar needs a labelled panel, not another constant.
#
# `MIN_MEAN_SCORE = 55` used to live here and warm_showcase.py enforced it. It
# could never fire: the mean it read is `normalize_similarity_score`, which
# FLOORS AT 60, so the check was `mean >= 60 < 55`. The 44.7 that calibrated it
# was measured when that field still carried the VBS value. Deleted 2026-08-11 —
# a gate that can only say "fine" is worse than no gate, because it reads like
# one is watching.

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
