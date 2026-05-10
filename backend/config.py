"""
Database configuration and session management
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool
import os
from dotenv import load_dotenv

load_dotenv()

# Security: Use async engine with connection pooling
DATABASE_URL = os.getenv("DATABASE_URL", "").replace("postgresql://", "postgresql+asyncpg://")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Clerk auth (per-instance JWKS URL, e.g. https://<instance>.clerk.accounts.dev/.well-known/jwks.json)
CLERK_JWKS_URL = os.getenv("CLERK_JWKS_URL", "")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Anonymous session signing key (httponly cookie for guest users)
_ANON_SESSION_DEFAULT = "vectorbox-anon-dev-secret"
ANON_SESSION_SECRET = os.getenv("ANON_SESSION_SECRET", os.getenv("SECRET_KEY", _ANON_SESSION_DEFAULT))
ANON_SESSION_MAX_AGE = 90 * 24 * 3600  # 90 days in seconds (7_776_000)
IS_PRODUCTION = os.getenv("ENVIRONMENT", "development") == "production"

# Refuse to boot in production with the dev-default cookie secret — anyone could
# forge anonymous sessions and hijack guest data.
if IS_PRODUCTION and ANON_SESSION_SECRET == _ANON_SESSION_DEFAULT:
    raise RuntimeError(
        "ANON_SESSION_SECRET (or SECRET_KEY) must be set in production; "
        "the dev default would let anyone forge guest cookies."
    )

# Cache versioning — bump to auto-invalidate all section/signal Redis keys on schema changes
FEED_CACHE_VERSION = "v2"

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
