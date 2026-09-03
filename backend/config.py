"""
Database configuration and session management
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
import os
from dotenv import load_dotenv

load_dotenv()

# Security: Use async engine with connection pooling
DATABASE_URL = os.getenv("DATABASE_URL", "").replace("postgresql://", "postgresql+asyncpg://")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Versión de la aplicación. El backend anunciaba "1.0.0" en dos sitios de
# `main.py` mientras el frontend iba por la 3.0.0 — dos deployables distintos,
# pero el número debe ser el mismo. Se sube A MANO aquí y en
# `frontend/package.json`, que son las dos únicas fuentes.
APP_VERSION = "3.1.0"

# Clerk auth (per-instance JWKS URL, e.g. https://<instance>.clerk.accounts.dev/.well-known/jwks.json)
CLERK_JWKS_URL = os.getenv("CLERK_JWKS_URL", "")

# Clerk issuer = the JWKS URL minus its `/.well-known/jwks.json` suffix
# (Clerk's `iss` claim is exactly the instance base URL). Used to pin the
# token's `iss` during JWT verification — defense-in-depth so a validly-signed
# token from a *different* Clerk instance can't be replayed against this API.
# `aud` is intentionally left unverified: Clerk session tokens don't carry it.
CLERK_ISSUER = os.getenv(
    "CLERK_ISSUER",
    CLERK_JWKS_URL.replace("/.well-known/jwks.json", "").rstrip("/") if CLERK_JWKS_URL else "",
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Anonymous session signing key (httponly cookie for guest users)
_ANON_SESSION_DEFAULT = "vectorbox-anon-dev-secret"
ANON_SESSION_SECRET = os.getenv("ANON_SESSION_SECRET", os.getenv("SECRET_KEY", _ANON_SESSION_DEFAULT))
# 30 days. The previous 90-day window was long enough that a stolen cookie
# survived three quarters of password-rotation hygiene, and an inactive guest
# could rack up taste data on a shared device with no expiry pressure.
ANON_SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days in seconds (2_592_000)
IS_PRODUCTION = os.getenv("ENVIRONMENT", "development") == "production"

# Refuse to boot in production with a guessable cookie secret. Checking only
# _ANON_SESSION_DEFAULT was NOT enough: setup.sh/setup.ps1 copy .env.example →
# .env, which ships `SECRET_KEY=your_secret_key_here` — a value published in the
# repo that is not the dev default, so it sailed past the old check. With a
# known key anyone can forge vb_anon_session cookies for sequential user ids,
# read/write those guest sessions, and then POST /auth/claim-anonymous to
# transfer the victim's whole rating history into their own account.
_WEAK_SECRETS = {
    _ANON_SESSION_DEFAULT,
    "your_secret_key_here",     # .env.example placeholder
    "change_me_in_prod",
    "changeme",
    "secret",
    "",
}
if IS_PRODUCTION and (
    ANON_SESSION_SECRET.strip().lower() in _WEAK_SECRETS or len(ANON_SESSION_SECRET) < 32
):
    raise RuntimeError(
        "ANON_SESSION_SECRET (or SECRET_KEY) is a placeholder or shorter than 32 "
        "chars. Generate one with `openssl rand -base64 32`; a guessable value "
        "lets anyone forge guest cookies and claim other users' rating history."
    )

# Refuse to boot in production with rate limiting disabled. TESTING_MODE
# silently kills the slowapi limiter — if it leaks into prod (via .env,
# docker-compose, or an inherited env var) the API has no rate limits at all.
if IS_PRODUCTION and os.getenv("TESTING_MODE", "False").lower() in ("true", "1", "yes"):
    raise RuntimeError(
        "TESTING_MODE must be False in production; otherwise slowapi rate "
        "limits are no-ops and every paid LLM endpoint is unmetered."
    )

# Refuse to boot in production with a Clerk DEV instance. pk_test_ / sk_test_
# keys come from Clerk's development instance which shows a banner, issues
# short-lived sessions, and is shared with whoever has dashboard access to
# that account — not safe as a production identity provider.
_clerk_publishable = os.getenv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "")
_clerk_secret = os.getenv("CLERK_SECRET_KEY", "")
if IS_PRODUCTION and (_clerk_publishable.startswith("pk_test_") or _clerk_secret.startswith("sk_test_")):
    raise RuntimeError(
        "Clerk development keys (pk_test_/sk_test_) detected in production. "
        "Provision a production Clerk instance and swap to pk_live_/sk_live_."
    )

# Cache versioning — bump to auto-invalidate all section/signal Redis keys on schema changes
FEED_CACHE_VERSION = "v3"  # v3: enriched-vector gate (2026-07-13)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Disable SQL logging in production
    pool_pre_ping=True,  # Verify connections before using
    pool_size=20,
    max_overflow=40,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()


async def get_db() -> AsyncSession:
    """Dependency for database sessions"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Initialize database tables"""
    # v1.1: Disabled to allow Alembic to manage schema
    # async with engine.begin() as conn:
    #     await conn.run_sync(Base.metadata.create_all)
    pass
