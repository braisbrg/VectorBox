# 🏛️ ARCHITECT: Stack Rules & Enforcement Layer

> **Role:** Chief Software Architect
> **Authority:** Final say on technology choices, forbidden patterns, and async discipline.
> **Last Updated:** 2026-03-04

This file serves as the **strict enforcement layer** for the VectorBox project. All code modifications must comply with these rules.

---

## 1. Technology Versions (Locked)

### Frontend Stack
| Package | Version | Notes |
| :--- | :--- | :--- |
| **Next.js** | `16.1.6` | App Router only |
| **React** | `19.2.4` | Concurrent features enabled |
| **Tailwind CSS** | `4.1.18` | CSS-First Architecture (`@theme`) |
| **Framer Motion** | `12.34.0` | All complex animations |
| **pnpm** | Latest | **Required** package manager |

### Backend Stack
| Package | Version | Notes |
| :--- | :--- | :--- |
| **FastAPI** | `0.122.0` | Async-first |
| **SQLAlchemy** | `2.0.44` | Async mode only |
| **Pydantic** | `2.x` | V2 syntax required |
| **Sentence-Transformers** | `all-MiniLM-L6-v2` | CPU-optimized |

### Infrastructure
| Component | Version | Notes |
| :--- | :--- | :--- |
| **PostgreSQL** | `15-alpine` | Production DB |
| **Qdrant** | Latest | Vector store |
| **Redis** | `7-alpine` | Cache layer |

---

## 2. Async Strictness Rules

### Database Session Management
> [!CAUTION]
> `AsyncSession` instances are **NOT thread-safe**.

**Requirement:** When running parallel tasks (e.g., `asyncio.gather`), you **MUST** create a fresh, isolated session for each task:

```python
# ✅ CORRECT: Fresh session per concurrent task
async def process_items(item_ids: list[int]):
    async def process_one(item_id: int):
        async with AsyncSessionLocal() as session:  # Fresh session!
            # ... do work
    
    await asyncio.gather(*[process_one(id) for id in item_ids])

# ❌ FORBIDDEN: Shared session across concurrent tasks
async def bad_process(db: AsyncSession, item_ids: list[int]):
    await asyncio.gather(*[
        do_work(db, id) for id in item_ids  # RACE CONDITION!
    ])
```

### Background Task Session Ownership
> [!CAUTION]
> Background tasks registered via `background_tasks.add_task()` **MUST NOT** receive the request-scoped `db: AsyncSession`. The session is torn down when the response is sent → `MissingGreenlet`.

**Pattern:** Background tasks create their own session:
```python
async def enrich_background(movie_ids: list[int]):
    async with AsyncSessionLocal() as db:  # Task owns this session
        # ... serial DB work here
```

### Qdrant Vector Database
- **Client:** Use `AsyncQdrantClient` exclusively.
- **Search API:** Use `await client.query_points(...)` (modern API).

> [!WARNING]
> The synchronous `QdrantClient` is **strictly forbidden** in async service layers. It blocks the event loop.

### Service Instantiation (Dependency Injection)
Heavy clients **MUST** be Singletons:
- `Qdrant` client
- `TMDB` client
- `EmbeddingModel` (SentenceTransformer)

**Reason:** Prevents resource exhaustion (HTTP connection pools, loaded ML models) during high concurrency.

**Pattern:** Inject via `dependencies.py` or initialize once at module level.
**CRITICAL BAN:** When instantiating services (e.g. `RecommendationService`) within endpoints or parallel tasks, you **MUST** pass these singletons down (via Dependency Injection). **NEVER** instantiate `TMDBClient()` directly inside a service's `__init__` method, as this defeats the singleton and triggers massive connection leaks.

---

## 3. Forbidden Patterns

### N+1 Query Explosion
> [!CAUTION]
> **NEVER** fetch related entities inside a loop.

```python
# ❌ FORBIDDEN: N+1 queries
for movie in movies:
    providers = await get_providers(movie.id)  # 100 movies = 100 queries!

# ✅ REQUIRED: Batch fetching
movie_ids = [m.id for m in movies]
providers = await session.execute(
    select(StreamingProvider).where(StreamingProvider.movie_id.in_(movie_ids))
)
```

### Blocking Calls in Async Context
- No `time.sleep()` → Use `asyncio.sleep()`
- No synchronous HTTP clients → Use `httpx.AsyncClient` or `AsyncOpenAI`
- No `QdrantClient` → Use `AsyncQdrantClient`

### Hanging Server-Side Fetches (Frontend)
- Next.js Server Components using `fetch` MUST include an `AbortController` bounded by `setTimeout`. Relying on default fetch infinite timeouts blocks internal worker threads and crashes deployments.

### Hardcoded Secrets & Console Leaks
- All secrets must come from environment variables.
- Never commit `.env` files (only `.env.example`).
- APIs and Request Interceptors MUST NOT expose internal variables or verbose errors to `console` in production bundles.

---

## 4. Package Management Rules

### Frontend (pnpm)
- **Lockfile:** `pnpm-lock.yaml` is the source of truth
- **Security:** `frontend/.npmrc` must include:
  ```
  frozen-lockfile=true
  audit=true
  ```
  This enforces lockfile integrity (supply chain protection).
- **Installs:** Local developers use `pnpm install --no-frozen-lockfile`. CI/Containers strictly use frozen lockfiles.

### Backend (pip)
- **Security:** Run `docker-compose exec backend python scripts/security_audit.py` before releases. Uses `--require-hashes` against `requirements.lock`.
- **Global Config:** `pip.conf` is mapped to `/app/pip.conf` and enforces Minimum Release Age of 720h (30 days). Dependabot configures per-category release ages.
- **Flag:** `pip install` always requires `--break-system-packages` flag inside containers.
- **Freezing:** Use `requirements.lock` generated by `pip-compile requirements.txt --generate-hashes -o requirements.lock` instead of raw `requirements.txt`.

---

## 5. Decision Authority

This Architect role has final authority on:
1. Technology stack changes (version upgrades, new libraries)
2. Architectural patterns (sync vs async, singleton scope)
3. Security policy changes
4. Performance optimization strategies

**Escalation Path:** Any deviation from these rules requires explicit Architect approval with documented rationale.

---

*This document supersedes conflicting guidance in other files.*
