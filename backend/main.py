"""
CineMatch AI - FastAPI Backend (Refactored to VectorBox)
"""
import logging
import os
import httpx
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Observability
from telemetry import setup_telemetry
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor

from database import init_db
from routers import upload, recommendations, tools, users, search, rss, auth, tasks, movies
from routers.similar import router as similar_router
from services.qdrant_service import QdrantService
from models.schemas import HealthResponse, RootResponse
from database import engine, Base
from dependencies import close_services, get_qdrant_service
from scheduler import start_scheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

from limiter import limiter


from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from redis import asyncio as aioredis

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info("Initializing VectorBox Backend...")

    # Observability: Initialize OTel tracer before anything else
    setup_telemetry()
    SQLAlchemyInstrumentor().instrument()
    RedisInstrumentor().instrument()

    # Initialize global HTTP client
    app.state.http_client = httpx.AsyncClient(
        timeout=15.0,
        limits=httpx.Limits(max_keepalive_connections=50, max_connections=100)
    )

    # Check DB Connection
    try:
        async with engine.begin() as conn:
            # We don't auto-create tables anymore to let Alembic handle migrations
            # If you want auto-creation back, uncomment:
            # await conn.run_sync(Base.metadata.create_all)
            pass
        logger.info("Database connection established.")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        
    # Initialize Redis Cache
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis = aioredis.from_url(redis_url, encoding="utf8", decode_responses=True)
    FastAPICache.init(RedisBackend(redis), prefix="fastapi-cache")
    logger.info(f"Redis Cache initialized at {redis_url}")
    
    # Initialize Qdrant collection
    qdrant = QdrantService()
    await qdrant.init_collection()
    
    # Start Scheduler
    start_scheduler()
    
    logger.info("VectorBox Backend initialized.")
    yield
    
    # Shutdown
    logger.info("Shutting down VectorBox Backend...")
    if hasattr(app.state, 'http_client'):
        await app.state.http_client.aclose()
    await close_services()


IS_PRODUCTION = os.getenv("ENVIRONMENT", "development") == "production"

app = FastAPI(
    title="VectorBox",
    description="Advanced movie recommendation system with semantic search",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if IS_PRODUCTION else "/api/docs",  # Restrict docs to /api path
    redoc_url=None if IS_PRODUCTION else "/api/redoc",
    openapi_url=None if IS_PRODUCTION else "/api/openapi.json"
)

# Observability: Auto-instrument all FastAPI routes (adds HTTP spans to every request)
FastAPIInstrumentor().instrument_app(app)

# Security: Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Security: Trusted Host Middleware (prevent host header attacks)
# Dynamically load from env for Cloudflare Tunnel and production domains
allowed_hosts_str = os.getenv("TRUSTED_HOSTS", "*")
allowed_hosts = [h.strip() for h in allowed_hosts_str.split(",") if h.strip()]
logger.info(f"Trusted hosts: {allowed_hosts}")

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=allowed_hosts
)

# Security: CORS with dynamic configuration for Hybrid Deployment
# Supports multiple origins for Vercel + Local development
allowed_origins_str = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
allowed_origins = [o.strip() for o in allowed_origins_str.split(",") if o.strip()]
logger.info(f"CORS allowed origins: {allowed_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
    max_age=600,  # Cache preflight for 10 minutes
)


# Security: Global exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors securely."""
    logger.warning(f"Validation error from {request.client.host}: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Invalid request data",
            "errors": [
                {
                    "field": ".".join(str(loc) for loc in err["loc"]),
                    "message": err["msg"]
                }
                for err in exc.errors()
            ]
        }
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch-all handler to prevent information leakage
    Security: Hide traceback in production
    """
    is_production = os.getenv("ENVIRONMENT", "development") == "production"
    
    if is_production:
        logger.error(f"Unhandled exception: {str(exc)}") # Log error but not full stack trace if sensitive? better to log full stack trace for admins but hide from user.
        # Actually standard practice is log full trace, return generic message.
        logger.error(f"Internal Server Error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal Server Error"}
        )
    else:
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc), "trace": str(exc)}
        )


# Health check endpoint (no rate limiting)
@app.get("/health", tags=["System"], response_model=HealthResponse)
async def health_check(qdrant: QdrantService = Depends(get_qdrant_service)) -> HealthResponse:
    """Health check for container orchestration"""
    # Deep Health Check
    health_status = {
        "status": "healthy",
        "service": "vectorbox-backend",
        "dependencies": {}
    }
    
    # 1. Postgres Check
    try:
        from config import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        health_status["dependencies"]["postgres"] = "ok"
    except Exception as e:
        health_status["dependencies"]["postgres"] = f"down: {str(e)}"
        health_status["status"] = "unhealthy"
        logger.error(f"Health Check Failed (Postgres): {e}")

    # 2. Redis Check
    try:
        from fastapi_cache import FastAPICache
        # Access the redis client from the backend
        redis_backend = FastAPICache.get_backend()
        # FastAPICache stores the client in .redis if it's RedisBackend, 
        # but let's try to ping if possible or just rely on the fact we initialized it.
        # Better: create a new connection or use the global one if approachable.
        # In lifespan we assigned it, but didn't store it globally accessible easily except via FastAPICache.
        # Actually proper way:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        r = aioredis.from_url(redis_url, encoding="utf8", decode_responses=True)
        await r.ping()
        await r.close()
        health_status["dependencies"]["redis"] = "ok"
    except Exception as e:
        health_status["dependencies"]["redis"] = f"down: {str(e)}"
        health_status["status"] = "unhealthy"
        logger.error(f"Health Check Failed (Redis): {e}")

    # 3. Qdrant Check
    try:
        await qdrant.client.get_collections()
        health_status["dependencies"]["qdrant"] = "ok"
    except Exception as e:
        health_status["dependencies"]["qdrant"] = f"down: {str(e)}"
        health_status["status"] = "unhealthy"
        logger.error(f"Health Check Failed (Qdrant): {e}")

    if health_status["status"] != "healthy":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=health_status
        )
        
    return HealthResponse(**health_status)


# Security: Add security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    return response

# Include routers
app.include_router(upload.router, prefix="/api/upload", tags=["Upload"])
app.include_router(recommendations.router, prefix="/api/recommendations", tags=["Recommendations"])
app.include_router(tools.router, prefix="/api/tools", tags=["Tools"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(search.router, prefix="/api/search", tags=["Search"])
app.include_router(similar_router, prefix="/api/recommendations", tags=["Recommendations"])
app.include_router(rss.router, prefix="/api/rss", tags=["RSS"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["Tasks"])
app.include_router(movies.router, prefix="/api/movies", tags=["Movies"])


@app.get("/", tags=["System"], response_model=RootResponse)
async def root() -> RootResponse:
    """API root endpoint"""
    return RootResponse(
        message="VectorBox API",
        version="1.0.0",
        docs="/api/docs"
    )
