"""
CineMatch AI - FastAPI Backend (Refactored to VectorBox)
"""
import asyncio
from config import APP_VERSION
import logging
import os
import httpx
from contextlib import asynccontextmanager

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
from routers import upload, recommendations, users, search, rss, auth, tasks, movies, onboarding, directors
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

    # El modelo de embeddings tarda ~5,3 s en cargar y hasta ahora lo pagaba el
    # primer usuario: medido, `/api/search/try` tras un reinicio tardaba 8,3 s
    # frente a 1,55 s la siguiente. Se carga aquí, en un hilo del executor para
    # no bloquear el loop, y FUERA del try de la base de datos — si Postgres no
    # responde, el buscador semántico sigue siendo lo que mejor puede hacer.
    try:
        from services.embedding_service import warmup as warm_embeddings
        await asyncio.get_running_loop().run_in_executor(None, warm_embeddings)
        logger.info("Embedding model warm.")
    except Exception as e:
        logger.error(f"Embedding model warmup failed: {e}")

    # Los otros dos costes de una sola vez que pagaba el primer usuario, medidos
    # 2026-08-04: el vocabulario léxico 208 ms (una agregación sobre 20k filas) y
    # construir el cliente de Groq 186 ms. Con el modelo ya calentado eran la
    # mayor parte del segundo y medio que quedaba sin explicar en la primera
    # petición. La tercera pata —la primera llamada a TMDB de proveedores, 459
    # ms— no se calienta: depende de qué película pida el usuario.
    try:
        from config import AsyncSessionLocal
        from services.lexical_channel import load_vocabulary
        from services.nlp_search import get_llm_client
        async with AsyncSessionLocal() as _db:
            await load_vocabulary(_db)
        await asyncio.get_running_loop().run_in_executor(None, get_llm_client)
        logger.info("Lexical vocabulary and LLM client warm.")
    except Exception as e:
        logger.error(f"Secondary warmup failed: {e}")
        
    # Initialize Redis — store as singleton on app.state so all request handlers
    # share the connection pool rather than creating new clients per request.
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis = aioredis.from_url(redis_url, encoding="utf8", decode_responses=True)
    app.state.redis = redis
    logger.info(f"Redis singleton initialized at {redis_url}")
    
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
    if hasattr(app.state, 'redis'):
        # redis-py 4.x async client only has close(); aclose() arrived in
        # 5.0.1. We pin redis==4.6.0 — calling aclose() unconditionally raised
        # AttributeError on every shutdown and skipped close_services() below.
        r = app.state.redis
        await (r.aclose() if hasattr(r, "aclose") else r.close())
        logger.info("Redis singleton closed.")
    await close_services()


IS_PRODUCTION = os.getenv("ENVIRONMENT", "development") == "production"

app = FastAPI(
    title="VectorBox",
    description="Advanced movie recommendation system with semantic search",
    version=APP_VERSION,
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
# Dev default is permissive; production MUST set TRUSTED_HOSTS explicitly.
_default_hosts = "*" if not IS_PRODUCTION else ""
allowed_hosts_str = os.getenv("TRUSTED_HOSTS", _default_hosts)
allowed_hosts = [h.strip() for h in allowed_hosts_str.split(",") if h.strip()]
logger.info(f"Trusted hosts: {allowed_hosts}")

if IS_PRODUCTION and (not allowed_hosts or '*' in allowed_hosts):
    raise RuntimeError(
        "TRUSTED_HOSTS must be set to an explicit allowlist in production "
        "(no '*'); host-header attacks are otherwise unblocked."
    )

if allowed_hosts and '*' not in allowed_hosts:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts
    )

# Security: CORS with dynamic configuration for Hybrid Deployment
# Supports multiple origins for Vercel + Local development
allowed_origins_str = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
allowed_origins = [o.strip() for o in allowed_origins_str.split(",") if o.strip()]
logger.info(f"CORS allowed origins: {allowed_origins}")

# Security (SEC-2): with allow_credentials=True a wildcard origin lets any site
# make credentialed cross-origin calls. Refuse to boot in production with an
# empty or wildcard allowlist — symmetric to the TRUSTED_HOSTS guard above.
if IS_PRODUCTION and (not allowed_origins or "*" in allowed_origins):
    raise RuntimeError(
        "ALLOWED_ORIGINS must be an explicit allowlist in production (no '*'); "
        "with allow_credentials=True a wildcard exposes credentialed CORS."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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
    Catch-all handler to prevent information leakage.

    Fail-safe: only treat the request as a dev environment when ENVIRONMENT
    is explicitly "development". Any other value — including unset, typo'd,
    or accidentally cleared — is treated as production and leaks nothing.
    """
    environment = os.getenv("ENVIRONMENT", "production")
    is_development = environment == "development"

    # Always log full traceback server-side. Server logs are not user-facing.
    logger.error(f"Unhandled exception ({request.method} {request.url.path}): {exc}", exc_info=True)

    if not is_development:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal Server Error"},
        )

    # Dev-only: include the exception message + class to speed up debugging.
    # We do NOT include a real traceback in the response body — that ships
    # source paths and module names to the browser, which is fine in dev
    # but trivially copy-pasted into a screenshot that ends up in a public
    # issue tracker. Keep it minimal even in dev.
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal Server Error",
            "dev_exception_class": type(exc).__name__,
            "dev_exception_message": str(exc),
        },
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
        # Status only. /health is unauthenticated and unmetered, so str(e) here
        # handed internal hostnames, ports and DB names to anyone during an
        # outage. The full exception still goes to the server log below.
        health_status["dependencies"]["postgres"] = "down"
        health_status["status"] = "unhealthy"
        logger.error(f"Health Check Failed (Postgres): {e}")

    # 2. Redis Check — reuse the lifespan singleton instead of ad-hoc connection
    try:
        redis = getattr(app.state, 'redis', None)
        if redis:
            await redis.ping()
        else:
            # Fallback during tests / cold boot
            r = aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), encoding="utf8", decode_responses=True)
            await r.ping()
            # redis-py renamed close()→aclose() at 5.0.1; support both so the
            # fallback health check doesn't itself error out (OBS-1).
            await (r.aclose() if hasattr(r, "aclose") else r.close())
        health_status["dependencies"]["redis"] = "ok"
    except Exception as e:
        health_status["dependencies"]["redis"] = "down"
        health_status["status"] = "unhealthy"
        logger.error(f"Health Check Failed (Redis): {e}")

    # 3. Qdrant Check
    try:
        await qdrant.client.get_collections()
        health_status["dependencies"]["qdrant"] = "ok"
    except Exception as e:
        health_status["dependencies"]["qdrant"] = "down"
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
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "magnetometer=(), gyroscope=(), accelerometer=()"
    )
    # `preload` only takes effect once the apex domain is submitted to
    # https://hstspreload.org — safe to advertise either way.
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains; preload"
    )
    # API responses never render HTML; the lockdown CSP is correct.
    # Docs routes (Swagger/ReDoc) are mounted only in non-production and
    # served by FastAPI itself — if you re-enable them in production,
    # carve out a route-specific exemption.
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response

# Include routers
app.include_router(upload.router, prefix="/api/upload", tags=["Upload"])
app.include_router(recommendations.router, prefix="/api/recommendations", tags=["Recommendations"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(search.router, prefix="/api/search", tags=["Search"])
app.include_router(similar_router, prefix="/api/recommendations", tags=["Recommendations"])
app.include_router(rss.router, prefix="/api/rss", tags=["RSS"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["Tasks"])
app.include_router(movies.router, prefix="/api/movies", tags=["Movies"])
app.include_router(directors.router, prefix="/api/directors", tags=["Directors"])
app.include_router(onboarding.router, prefix="/api/onboarding", tags=["Onboarding"])

@app.get("/api/health", tags=["System"], include_in_schema=False)
async def api_health_alias(qdrant: QdrantService = Depends(get_qdrant_service)):
    """Alias so frontend /api/health calls don't 404.

    OBS-1: delegate to the real deep check instead of returning a static
    {"status":"healthy"}. The previous stub always reported healthy, so any
    monitor/LB pointed at /api/health saw green during a Postgres/Redis/Qdrant
    outage.
    """
    return await health_check(qdrant=qdrant)


@app.get("/", tags=["System"], response_model=RootResponse)
async def root() -> RootResponse:
    """API root endpoint"""
    return RootResponse(
        message="VectorBox API",
        version=APP_VERSION,
        docs="/api/docs"
    )
