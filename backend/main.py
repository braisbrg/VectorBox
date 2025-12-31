"""
CineMatch AI - FastAPI Backend
"""
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from database import init_db
from routers import upload, recommendations, tools, users, search, rss
from routers.similar import router as similar_router
from services.qdrant_service import QdrantService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Rate limiter for DDoS protection
limiter = Limiter(key_func=get_remote_address)


from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from redis import asyncio as aioredis

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan events"""
    # Startup
    logger.info("Initializing CineMatch AI...")
    await init_db()
    
    # Initialize Redis Cache
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis = aioredis.from_url(redis_url, encoding="utf8", decode_responses=True)
    FastAPICache.init(RedisBackend(redis), prefix="fastapi-cache")
    logger.info(f"Redis Cache initialized at {redis_url}")
    
    # Initialize Qdrant collection
    qdrant = QdrantService()
    await qdrant.init_collection()
    
    # Start Scheduler
    from scheduler import start_scheduler
    start_scheduler()
    
    logger.info("Application started successfully")
    yield
    
    # Shutdown
    logger.info("Shutting down CineMatch AI...")


app = FastAPI(
    title="CineMatch AI",
    description="Advanced movie recommendation system with semantic search",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",  # Restrict docs to /api path
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# Security: Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Security: Trusted Host Middleware (prevent host header attacks)
# Dynamically load from env for Cloudflare Tunnel and production domains
allowed_hosts_str = os.getenv("TRUSTED_HOSTS", "localhost,127.0.0.1")
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
    """Catch-all handler to prevent information leakage"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal error occurred"}
    )


# Health check endpoint (no rate limiting)
@app.get("/health", tags=["System"])
async def health_check():
    """Health check for container orchestration"""
    return {"status": "healthy", "service": "cinematch-backend"}


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
app.include_router(search.router, prefix="/api/search", tags=["Search"])
app.include_router(similar_router, prefix="/api/recommendations", tags=["Recommendations"])
app.include_router(rss.router, prefix="/api/rss", tags=["RSS"])


@app.get("/", tags=["System"])
async def root():
    """API root endpoint"""
    return {
        "message": "CineMatch AI API",
        "version": "1.0.0",
        "docs": "/api/docs"
    }
