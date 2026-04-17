# ⚙️ BACKEND DIRECTOR: FastAPI, Postgres & Python Rules

> **Role:** Backend Technical Lead
> **Domain:** API Design, Database, Async Patterns, Authentication
> **Last Updated:** 2026-04-10

This file contains all backend-specific rules, database patterns, and API guidelines for the VectorBox project.

---

## 1. Technology Stack

### Core Framework
| Technology | Version | Purpose |
| :--- | :--- | :--- |
| **FastAPI** | `0.135.1` | Async-first web framework |
| **Uvicorn** | Standard Worker | ASGI server |
| **Pydantic** | `V2` | Request/response validation |

### Database Layer
| Technology | Version | Purpose |
| :--- | :--- | :--- |
| **PostgreSQL** | `15-alpine` | Primary relational database |
| **SQLAlchemy** | `2.0.x` | Async ORM |
| **asyncpg** | Latest | Non-blocking Postgres driver |

### AI/ML Layer
| **Groq** | Provider | LLM inference |
| **Llama 4 Scout** | `17b-16e-instruct` | Fast search intent parsing |
| **Llama 3.3** | `70b-versatile` | Deep analysis, re-ranking |
| **GPT-OSS** | `120b` | Groq fallback |
| **Instructor** | Latest | Structured JSON output from LLMs |
| **Sentence-Transformers** | `all-MiniLM-L6-v2` | Local embedding generation (CPU) |

> **Note:** All LLM / Instructor code **MUST** use the `AsyncOpenAI` client (from `openai import AsyncOpenAI`). The synchronous `OpenAI` client is strictly forbidden inside async functions to prevent event loop blocking.

---

## 2. Async Session Management

> [!CAUTION]
> `AsyncSession` instances are **NOT thread-safe**.

### The Rule
When running parallel tasks (e.g., `asyncio.gather`), create a **fresh, isolated session** for each task:

```python
# ✅ CORRECT: Fresh session per concurrent task
async def process_items(item_ids: list[int]):
    async def process_one(item_id: int):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Movie).where(Movie.id == item_id))
            # ... process
    
    await asyncio.gather(*[process_one(id) for id in item_ids])
```

### The Anti-Pattern
```python
# ❌ FORBIDDEN: Shared session across concurrent tasks
async def bad_process(db: AsyncSession, item_ids: list[int]):
    await asyncio.gather(*[
        fetch_movie(db, id) for id in item_ids  # RACE CONDITION!
    ])
```

### Background Task Session Ownership
> [!CAUTION]
> Background tasks MUST own their own `AsyncSession`. NEVER reuse the request-scoped `db` session, as it is torn down when the HTTP response returns (causing `MissingGreenlet` errors).

```python
# ✅ CORRECT: Background task creates its own session
async def my_bg_task(user_id: int):
    from config import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        # DB logic here

# ❌ FORBIDDEN
background_tasks.add_task(my_bg_task, db=db) 
```

---

## 3. Database Access Patterns

### Batch Fetching (Required)
Always use batch queries for list endpoints:

```python
# ✅ CORRECT: Single query with IN clause
movie_ids = [m.id for m in movies]
result = await session.execute(
    select(Movie).where(Movie.id.in_(movie_ids))
)
movies = result.scalars().all()
```

### N+1 Query Prevention
> [!WARNING]
> Never fetch related entities inside a loop.

```python
# ❌ FORBIDDEN: N+1 explosion
for movie in movies:
    providers = await get_providers(movie.id)  # 100 queries!

# ✅ CORRECT: Batch with join or separate IN query
providers = await get_providers_batch(movie_ids)
```

### Key Indexes
The following indexes are critical for performance:
- `vectorbox_score` (filtering quality)
- `popularity` (sorting)
- `vote_count` (validity filtering)

### UserRating Schema
Key columns: `is_watched`, `is_liked`, `is_watchlist`, `is_rejected`, `rating`, `watched_date`, `watch_count` (INTEGER DEFAULT 1), `review`.
- `watch_count` is populated from `diary.csv` during ZIP import (each diary row = +1) and incremented on RSS rewatch detection.
- `is_liked` is set ONLY by ZIP import (likes/films.csv processed last). The RSS upsert MUST NOT include `is_liked` in its `on_conflict_do_update` SET clause — RSS carries no liked status.

### CSV Key Normalization
`DataProcessor._get_key(row)` builds `"{title}_{year}"`. **Always normalize year** via `int(float(raw_year))` — pandas promotes Year to float64 when any row has a null year, causing key mismatches between CSVs that break is_liked matching.

### Code Conventions — ID Types
VectorBox uses TWO different movie ID spaces. Mixing them causes silent deduplication failures.
- `internal_id` → `Movie.id` (PostgreSQL auto-increment). Used for `watched_ids` and `UserRating.movie_id`.
- `tmdb_id` → `Movie.tmdb_id` (TMDB API identifier). Used for `seen_ids` and Qdrant indexing.
- **Rule:** When comparing against `seen_ids`: always use `m.tmdb_id`.

### Code Conventions — Async & SQLAlchemy
- **Rule:** ALL boolean column comparisons use `.is_()` not `==`. 
  - ✅ `UserRating.is_watched.is_(True)` | ❌ `UserRating.is_watched == True`
- **Rule:** ALL HTTP clients must be explicitly closed after use (e.g., `await tmdb.aclose()`). Never let them go out of scope silently.
- **Rule:** CPU-bound work (KMeans, MMR reranking) must be offloaded using `await loop.run_in_executor(None, cpu_bound_func, args)`. Calling blocking CPU functions directly in an async context is strictly forbidden.

---

## 4. Service Architecture

### Singleton & Dependency Injection (Mandatory)
Heavy clients **MUST** be Singletons to prevent resource exhaustion:

| Service | Reason |
| :--- | :--- |
| `QdrantClient` | Connection pool management |
| `TMDBClient` | HTTP session reuse and rate limit backoff |
| `OMDbClient` | HTTP session reuse |
| `EmbeddingModel` | ML model loaded once in memory |
| `httpx.AsyncClient` | Global connection pooling (via lifespan state) |

**Implementation:** Inject via `dependencies.py` or initialize at module level.
**Injection:** **MUST** be passed down to downstream business logic. **DO NOT** instantiate `TMDBClient()` directly inside `FeedService` or `RecommendationService` or any parallel tasks, as this creates rampant connection leaks.

### Directory Structure
```
backend/
├── routers/       # API endpoints
│   ├── recommendations.py
│   ├── search.py
│   └── auth.py
├── services/      # Business logic
│   ├── feed_service.py      # Home feed orchestration
│   ├── recommendation_engine.py # Algorithms for feed sections
│   ├── movie_factory.py     # Centralized ingestion pipeline (v1.4: ensures metadata resilience)
│   ├── clustering_service.py # K-Means, MMR
│   ├── nlp_search.py        # "Magic Box" LLM logic
│   └── rss_service.py       # Letterboxd sync
├── models/        # Data models
│   ├── database.py          # SQLAlchemy models
│   ├── schemas.py           # API Pydantic schemas
│   └── external_schemas.py  # Types for OMDb/Qdrant
└── scripts/       # Maintenance tasks
```

---

## 5. Authentication Model (v1.2)

### Netflix-Style Profiles
- **Username:** User-chosen display name
- **PIN:** 4-digit numeric passcode
- **Hashing:** `passlib[bcrypt]` for PIN storage

### Session Management
- **Token:** Long-lived `secret_token` (UUID)
- **Storage:** HTTP-only cookies
- **Lifetime:** Extended (no frequent re-auth)

### Logging Hygiene
- **Rule:** **NEVER** log raw session cookies (`vectorbox_token`), raw Authorization headers containing Bearer tokens, or plain-text PINs. Redact these values in all middleware or auth routers before logging.

### Letterboxd Linking
- **Decoupled:** VectorBox username ≠ Letterboxd username
- **Flow:** Users link their Letterboxd profile separately via settings

### IDOR Protection (v1.2)
- **Identity Derivation:** Never pass `user_id` as a client argument (query/path) for protected resources.
- **Enforcement:** Use `dependencies.get_current_user` and `dependencies.verify_user_ownership`.

---

## 6. Caching Strategy

### Redis Usage
| Use Case | TTL | Key Pattern |
| :--- | :--- | :--- |
| Feed sections | 1 hour | `feed:{user_id}:{section}` |
| Profile Summary | 24 hours | `profile_summary:{user_id}` |
| TMDB responses | 24 hours | `tmdb:{endpoint}:{id}` |
| Provider availability | 24 hours | `providers:{movie_id}:{region}` |
| Trending cache | 24 hours | `trending:letterboxd` |

### Cache Guard
- **Rule**: Never cache feeds with < 3 sections to Redis.

### Cache Invalidation
Invalidate user-specific caches when:
- User imports new ratings (`reset_profiles.py`)
- User reclusters (`ClusteringService`)
- RSS sync updates watched list

---

## 7. LLM Integration (Cascading Fallback)

### Priority Order
1. **Tier 1 (Speed):** Groq `meta-llama/llama-4-scout-17b-16e-instruct`
   - Use for: Real-time search bar intent parsing
2. **Tier 2 (Intelligence):** Groq `llama-3.3-70b-versatile`
   - Use for: Deep analysis, detailed reasoning & Tier 1 retry
3. **Tier 3 (Groq Fallback):** Groq `openai/gpt-oss-120b`
   - Use for: High throughput fallback before leaving Groq completely

### Structured Output
- **Library:** `instructor` with Pydantic models
- **Schema:** `MovieSearchIntent` for query parsing
- **Logic:** "Broad Search" - LLM expands keywords into synonyms

---

## 8. Package Management

### Dependencies (pip)
- **Global Config:** `pip.conf` is mapped to `/app/pip.conf` (via `PIP_CONFIG_FILE` in `docker-compose.yml`) and enforces a Minimum Release Age of 720h (30 days).
- **Dependabot:** Configures per-category release ages in `.github/dependabot.yml`.
- **Installs:** `pip install` **MUST ALWAYS** use `--break-system-packages` flag inside containers.
- **Lockfile Regeneration:** After changing `requirements.txt`, strictly run:
  `docker-compose exec backend pip-compile requirements.txt --generate-hashes -o requirements.lock`

---

*For architectural enforcement rules, see [architect.md](../c-suite/architect.md).*
*For data science algorithms, see [data-science.md](data-science.md).*
