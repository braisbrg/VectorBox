"""
Rate Limiter Configuration
Uses Redis backend for distributed rate limiting across workers.

Resolves the real client IP from CF-Connecting-IP / X-Forwarded-For so that
limits actually apply per user behind Cloudflare Tunnel + Vercel, instead
of bucketing every request under the proxy egress IP.
"""
import os
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

_ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
_IS_PRODUCTION = _ENVIRONMENT == "production"
_IS_TESTING = os.getenv("TESTING_MODE", "False").lower() in ("true", "1", "yes")

# Only allow the limiter to be disabled in development. config.py raises a
# RuntimeError at import time if TESTING_MODE is truthy in production, so this
# is a belt-and-braces safety net.
_LIMITER_ENABLED = _IS_PRODUCTION or not _IS_TESTING


def client_ip(request: Request) -> str:
    """Resolve the real client IP behind a proxy chain.

    Priority:
      1. CF-Connecting-IP (Cloudflare Tunnel — single value, trusted)
      2. X-Forwarded-For first hop (generic reverse-proxy header)
      3. request.client.host (direct connection fallback)

    In production this is essential — otherwise slowapi keys every request
    under the tunnel's egress IP and all users share one bucket.

    ⚠ DECIDE THIS AT DEPLOY TIME — the priority above is only correct when the
    browser talks to the backend DIRECTLY (through Cloudflare). It is WRONG if
    /api/* is proxied server-side by the frontend, which is what
    next.config.js `rewrites` does when the app runs on Vercel: Cloudflare then
    sets CF-Connecting-IP to VERCEL's egress, so every user in the world keys to
    one bucket and a single abuser exhausts /rss/sync (2/hour), /search/natural
    and /upload/export for everybody. The real client IP is in the first
    X-Forwarded-For hop, which this function only reaches on a CF miss.

    Do NOT "fix" this blind: if the browser does hit the backend directly,
    trusting the first XFF hop turns a shared bucket into a trivially spoofable
    bypass (any client can send the header), which is strictly worse.

    Verify with ONE real request from a phone on mobile data, then choose:
        logger.warning(f"CF={request.headers.get('CF-Connecting-IP')} "
                       f"XFF={request.headers.get('X-Forwarded-For')}")
      - CF == your phone's IP        -> leave this function exactly as is.
      - CF == a Vercel/datacenter IP -> read the first XFF hop first, and treat
                                        CF-Connecting-IP as the fallback.
    """
    cf = request.headers.get("CF-Connecting-IP")
    if cf:
        return cf.strip()
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(
    key_func=client_ip,
    storage_uri=REDIS_URL,
    strategy="fixed-window",
    enabled=_LIMITER_ENABLED,
)
