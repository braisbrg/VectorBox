---
# Agent Instructions — VectorBox

> ⚠️ BRANCHING RULE: Never commit directly to `main`. Use `develop` or `feature/*` branches. See Git Workflow section below.

## Git Workflow

### Branch Structure
- `main` — stable, production-ready code only. NEVER commit directly to main.
- `develop` — active development branch. All work starts here.
- `feature/name` — temporary branches for significant features or changes. Branch from develop, merge back to develop when complete.

### Rules (mandatory)
- Never commit directly to `main`. All changes go through `develop` first.
- Before starting any significant change (new feature, refactor, algorithm improvement), create a feature branch from `develop`:
```bash
  git checkout develop
  git checkout -b feature/descriptive-name
```
- Commit messages must follow this format:
  - `feat: description` — new feature
  - `fix: description` — bug fix
  - `refactor: description` — code restructure without behavior change
  - `perf: description` — performance improvement
  - `docs: description` — documentation only

### Merging to main (Releases only)
- Only merge `develop` → `main` when the code is stable and tested.
- Every merge to `main` must be tagged with a semantic version:
```bash
  git checkout main
  git merge develop
  git tag -a v1.X.Y -m "Brief description of what this release contains"
  git push origin main
  git push origin v1.X.Y
```
- Version convention (SemVer):
  - **Major** (v2.0.0): breaking changes, DB migrations that require data wipe
  - **Minor** (v1.4.0): new features, algorithm improvements, new UI sections
  - **Patch** (v1.3.1): bug fixes, hotfixes, small corrections

### Current branch state
- `main` — last stable release
- `develop` — active development

### Agent instructions
- When implementing features or fixes, always ask which branch is currently active before making changes.
- Never suggest or execute `git push origin main` directly — always push to the current feature branch or `develop`.
- When a feature is complete, remind the user to merge to `develop` and, if stable, tag a release on `main`.

## Package Manager
- Frontend uses **pnpm**, NOT npm and NOT yarn
- Always use `pnpm install`, `pnpm add`, `pnpm run`
- Never use `npm install` or `npm run`
- pnpm lockfile: `frontend/pnpm-lock.yaml`
- Local installs only: `pnpm install --no-frozen-lockfile`
- CI/container installs use frozen lockfile (enforced in .npmrc)

## Stack
- Backend: FastAPI (Python 3.11), Docker via docker-compose
- Frontend: Next.js 16, Tailwind CSS v4, TypeScript
- DB: PostgreSQL 15 + Qdrant + Redis 7
- Package install inside containers:
  always use --break-system-packages flag for pip

## Docker
- Always use `docker-compose exec backend ...` for backend commands
- Never install packages directly on host for backend
- Backend lock file is hash-verified: backend/requirements.lock
- Models Cache: Persistent `models_cache` volume mounted to `/models_cache`
- Regenerate lock file with:
    pip-compile requirements.txt --generate-hashes -o requirements.lock

## Python
- pip install always needs --break-system-packages flag
- Lock file: backend/requirements.lock (hash-verified)
- pip.conf location: backend/pip.conf (mounted as /app/pip.conf)
- pip config injected via PIP_CONFIG_FILE=/app/pip.conf in docker-compose.yml
- Global pip minimum-release-age: 720h (30 days)
- Per-category release ages enforced via Dependabot cooldown
  (see .github/dependabot.yml groups)

---

## Architecture — Trident Engine

VectorBox uses a 3-signal hybrid recommendation engine (Trident):
- Signal A `because_you_watched` — Item-Item Collaborative Filtering
  via EmbeddingService + Qdrant nearest-neighbor search.
  Embeddings are generated from **LLM-enriched cinematic descriptions** (tone, pacing, style) via Groq (Scout/70B/8B).
  Seeds from movies rated 4+ stars OR explicitly liked (is_liked). Applies anti-vector penalties.
  **Quality floor:** candidates filtered by `vectorbox_score IS NOT NULL AND vectorbox_score >= 55`.
  **Coherence threshold:** `score_threshold=0.25` (vector similarity) to keep results tightly anchored.
  **Anchor pool:** fetches up to 100 candidates without ORDER BY (Python scoring selects best anchor).
  **Contributors:** each result carries `[{"type": "anchor", "seed_title": ..., "seed_year": ..., "seed_rating": ..., "similarity": ...}]`.
- Signal B `your_taste` — **K-Medoids cluster search**, pointing to a real movie (`medoid_movie_id`).
  Clusters are labeled dynamically by Groq (e.g., "A24 Dread") with dominant genre filtering.
  **Automated Rotation**: Automatically cycles through user's clusters on each feed fetch using a Redis counter.
  **Quality floor:** same `vectorbox_score >= 55` filter applied.
  **Genre coherence (EXCLUSION_PAIRS):** niche genres (Animation, Family, Horror) excluded if cluster doesn't support them. This filter ONLY applies here — NOT in hybrid_reranking (Picked For You).
  **Contributors:** each result carries `[{"type": "cluster", "cluster_name": ..., "medoid_title": ..., "similarity": ...}]`.
  Penalized against Anti-vector of low-rated/rejected films, applying MMR based on dominant cluster genres.
- Signal Auteur `get_signal_b_auteur` (Directors + Cast) — Director/Actor analysis
  Uses a `_compute_auteur_signal_raw()` helper applying a weighted point system (5★→2.0, 4.5★→1.5) triggering at 3.0 pts for directors, 2.5 pts for actors.
- Signal C `hidden_gems` — **DB-First Discovery**
  Identifies high-quality niche films directly from Postgres with DYNAMIC thresholds based on user's movie count:
    Cold start (<30 movies): score>60, popularity<40, votes>200
    Growing (30-99):         score>65, popularity<30, votes>300
    Rich (100+):             score>75, popularity<20, votes>500
  Uses an Exoticism Boost (`+15%`) for non-English films. Re-ranked using 30% vector similarity weight.

- Picked For You (`hybrid_reranking`) — Trident RRF fusion of signals A (vibe), Auteur (director/actor), and C (hidden gems).
  **Contributors:** normalized percentage breakdown per signal: `[{"type": "vibe"|"auteur"|"crowd", "label": ..., "score": 0.0-1.0}]` where scores sum to 1.0.
  Percentage hidden in UI when only a single signal contributed (no "100%" shown).
  EXCLUSION_PAIRS genre filter is ABSENT here — only MIN_QUALITY_SCORE=55 applies.

Results fused via RRF (Reciprocal Rank Fusion) +
Sigmoid quality weighting on VectorBox Score (0–100).
Magic Box intent `quality_gate_bypass` bypasses normal bounds (midpoint 65) explicitly allowing "trashy" responses by dropping the midpoint to 25.

Feed orchestration: `FeedService.get_main_feed()` runs **11 tasks
in parallel** via `asyncio.gather()`. Each task opens its own
isolated `AsyncSessionLocal()` session — they NEVER share sessions.

**Cache Guard**: Feeds with < 3 sections are NOT saved to Redis. Feed caches are explicitly wiped `_invalidate_feed_cache()` (which scans and clears `section:*` keys) after RSS sync. The feed is cached on a per-section basis with discrete TTL limits targeting optimum freshness.



---

## Architecture — Dependency Injection

All services must be injected, never instantiated inside handlers.

Singletons registered in `backend/dependencies.py`:
  get_tmdb_client()        → TMDBClient
  get_omdb_client()        → OMDbClient
  get_qdrant_service()     → QdrantService
  get_embedding_service()  → EmbeddingService
  get_http_client()        → httpx.AsyncClient (Global lifespan client)

Router endpoints receive services via Depends():
  db:     AsyncSession  = Depends(get_db)
  tmdb:   TMDBClient    = Depends(get_tmdb_client)
  qdrant: QdrantService = Depends(get_qdrant_service)

FeedService and RecommendationEngine are instantiated
per-request but receive injected clients as constructor params.

ProviderService always requires BOTH db AND tmdb:
  local_provider = ProviderService(session, tmdb)

ClusteringService accepts optional qdrant:
  clustering = ClusteringService(qdrant=qdrant)

---

## Background Tasks — Correct Pattern

Background tasks MUST own their own AsyncSession.
NEVER reuse the request-scoped db session — it is torn
down when the HTTP response returns (MissingGreenlet error).

CORRECT pattern:
  async def my_background_task(user_id: int, data: list):
      from config import AsyncSessionLocal
      async with AsyncSessionLocal() as session:
          service = MyService(session)
          try:
              await service.do_work(data)
              await session.commit()
          except Exception as e:
              logger.error(f"Task failed: {e}")
              # Never re-raise — background tasks must not crash

CORRECT registration:
  background_tasks.add_task(my_background_task, user_id, data)
  # NOT: background_tasks.add_task(my_background_task(user_id, data))

If a background task spawns concurrent subtasks via
asyncio.gather(), each subtask MUST open its own separate
AsyncSessionLocal() — never share one session across gather().

Shared HTTP clients (TMDBClient) CAN be passed into subtasks —
they are HTTP-safe. DB sessions cannot.

---

## Code Conventions — Async & SQLAlchemy

- ALL database calls use async SQLAlchemy (await db.execute(...))
- ALL boolean column comparisons use .is_() not ==:
    ✅  UserRating.is_watched.is_(True)
    ❌  UserRating.is_watched == True

- ALL HTTP clients must be explicitly closed after use:
    ✅  await tmdb.aclose()
    ❌  letting them go out of scope silently

- EmbeddingService loads SentenceTransformer on init (expensive).
  NEVER instantiate inside a loop or per-request.
  Use the singleton: get_embedding_service() via Depends().

- Qdrant vector fetches must use batch methods:
    ✅  await qdrant.get_vectors_batch(tmdb_ids)
    ❌  await qdrant.get_vector(id) inside a for loop (N+1)

- Provider fetches must use batch methods:
    ✅  await provider_service.get_providers_batch(ids, country)
    ❌  await provider_service.get_providers(id, country) in loop

- CPU-bound work (KMeans, MMR reranking) must be offloaded:
    ✅  await loop.run_in_executor(None, cpu_bound_func, args)
    ❌  calling blocking CPU functions directly in async context

---

## Code Conventions — ID Types

VectorBox uses TWO different movie ID spaces. Mixing them
causes silent deduplication failures.

  internal_id  →  Movie.id        PostgreSQL auto-increment
  tmdb_id      →  Movie.tmdb_id   TMDB API identifier

Rules:
  seen_ids set (feed deduplication) → stores tmdb_id
  watched_ids set                   → stores internal id
  UserRating.movie_id               → FK to Movie.id (internal)
  Qdrant vectors indexed by         → tmdb_id

### Qdrant Upsert Check
`qdrant.upsert_batch` supports a `check_exists=True` flag. When enabled, it performs a scroll check to see if the payload is identical before writing. Use this in parallel ingestion paths to avoid redundant I/O.

### Backup & Restore
- `backup_manager.py`: Dumps Postgres, Qdrant snapshots, and Redis `BGSAVE`. Rotates to last 5.
- `restore_manager.py`: Orchestrates full system restoration from ZIP. Supports `--dry-run`.
- `reconcile_letterboxd_movies.py`: Audits `letterboxd_uri` movies, verifies `tmdb_id` via TMDB search. `--fix` re-ingests and migrates ratings.
- `fix_movies_manual.py`: Applies manual CSV corrections (`letterboxd_uri,correct_tmdb_id,old_tmdb_id`). Supports `--dry-run` and `--file`.
- `reenrich_movies.py`: Targets scored movies missing IMDb data and re-runs enrichment.
- Host wrappers: `backup.ps1` / `backup.sh` for easy CLI access.

When comparing against seen_ids:    always use m.tmdb_id
When comparing against watched_ids: always use m.id

---

## Anti-Patterns — Never Do These

All of these have been found and fixed. Do not reintroduce.

1. SINGLETON BYPASS
   ❌  TMDBClient() / QdrantService() / EmbeddingService()
       instantiated inside a route handler or loop
   ✅  Always use Depends() or receive via constructor param

2. SESSION SHARING IN PARALLEL TASKS
   ❌  asyncio.gather(task(db), task(db)) — shared session
   ✅  Each task: async with AsyncSessionLocal() as session

3. SINGLETON CLOSE IN REQUEST SCOPE
   ❌  await qdrant.close() in a finally block inside a route
       (kills the singleton for all subsequent requests)
   ✅  Never close injected singletons

4. N+1 QUERIES
   ❌  for movie in movies: await qdrant.get_vector(movie.id)
   ❌  for movie in movies: await provider_service.get_providers(...)
   ✅  Always use batch methods before the loop

5. BACKGROUND TASK REUSING REQUEST SESSION
   ❌  background_tasks.add_task(enrich, db=db)
       where db is the request-scoped AsyncSession
   ✅  Background task creates its own AsyncSessionLocal()

6. IDOR VULNERABILITY
   ❌  Accepting user_id from query params without auth check
   ✅  Always derive identity from token:
       current_user = Depends(get_current_user)

7. BLOCKING CALLS IN ASYNC CONTEXT
   ❌  client = OpenAI()      (sync client)
   ✅  client = AsyncOpenAI() (async client)

8. ID TYPE CONFUSION IN seen_ids
   ❌  if m.id not in seen_ids  (internal id vs tmdb set)
   ✅  if m.tmdb_id not in seen_ids

9. REROLL WITHOUT UNMOUNT GUARD (React)
   ❌  setState after async call with no isMounted check
   ✅  Use isMounted ref + cleanup useEffect

10. SHARED SESSION IN TRIDENT / SIGNAL GATHER
    ❌  asyncio.gather(signal_a(db), signal_b(db), signal_c(db))
        where db is a single shared AsyncSession
    ✅  Each signal task wraps its own AsyncSessionLocal():
        async def measure_signal(fn):
            async with AsyncSessionLocal() as session:
                return await fn(session)
        asyncio.gather(
            measure_signal(signal_a),
            measure_signal(signal_b),
            measure_signal(signal_c)
        )
    Note: HTTP clients (TMDBClient, QdrantService) ARE safe
    to share across gather tasks. Only DB sessions are not.

11. EXCLUSION_PAIRS IN HYBRID_RERANKING
    ❌  Applying EXCLUSION_PAIRS genre filter inside hybrid_reranking (Picked For You)
        → reduces candidate pool to near zero ("5 movies remain" observed in logs)
    ✅  EXCLUSION_PAIRS belongs ONLY in get_your_taste_section

12. is_liked IN RSS UPSERT SET CLAUSE
    ❌  Including "is_liked": excluded.is_liked in on_conflict_do_update for RSS
        → RSS items never carry liked status, silently resets ZIP-imported likes to False
    ✅  Omit is_liked from RSS upsert SET; only ZIP upload controls is_liked

13. REDIS r.keys() IN ASYNC CONTEXT
    ❌  keys = await r.keys("section:*")
        → scans the entire keyspace in a single blocking O(N) call;
          hangs the Redis event loop under production load
    ✅  Use SCAN loop:
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match=f"section:{FEED_CACHE_VERSION}:{user_id}:*", count=100)
            if keys:
                await r.delete(*keys)
            if cursor == 0:
                break

14. UNVERSIONED CACHE KEYS
    ❌  cluster_rotation:{user_id}  (no version prefix)
        → on FEED_CACHE_VERSION bump, section keys are wiped but the rotation
          counter persists, causing stale cluster cycling state
    ✅  cluster_rotation:{FEED_CACHE_VERSION}:{user_id}
        Both sides of the cache (section keys AND cluster_rotation)
        MUST use the same FEED_CACHE_VERSION prefix from feed_service.py.

15. TYPESCRIPT TYPE ERASURE
    ❌  (obj as any).field — casting to any to access a known property
    ❌  interface Foo { [key: string]: any } — open index signature on typed models
    ❌  setState(x as any as TargetType)   — double cast to force assignment
    ✅  Add the missing field to the proper interface (e.g. has_data?: boolean on UserSession)
    ✅  Declare all fields explicitly on request/response interfaces (e.g. SearchIntent)
    ✅  Remove dead fallback paths driven by wrong types (e.g. (movie as any).poster_path
        when the API always returns poster_url)

---

## Data Integrity — Letterboxd URI Normalization

All `letterboxd_uri` values MUST be canonical:
  https://letterboxd.com/film/{slug}/

CSVParser.normalize_letterboxd_uri() handles this:
  /username/film/slug/ → /film/slug/  (converted)
  boxd.it/...          → None         (rejected)
  /tmdb/12345          → None         (rejected)

Applied automatically in parse_ratings_csv and parse_watched_csv.

## Data Integrity — CSV Key Normalization

`DataProcessor._get_key(row)` builds the lookup key as `"{title}_{year}"`.
**CRITICAL:** pandas promotes the Year column to float64 (e.g. 1991.0) when any
row in the CSV has a missing year. Always normalize via `int(float(raw_year))`
to prevent key mismatches between CSVs (e.g. "My Girl_1991.0" vs "My Girl_1991").
The current implementation in `data_processor.py` handles this correctly.

## Data Integrity — RSS Sync & is_liked

The RSS upsert in `rss_service.py` MUST NOT include `is_liked` in the
`on_conflict_do_update` SET clause. RSS feeds carry no liked-film data, so
including it would overwrite ZIP-imported likes with False on every sync.
Current implementation omits `is_liked` from the RSS upsert SET.

## UserRating Schema

`user_ratings` table columns include `watch_count INTEGER DEFAULT 1`:
- Populated from `diary.csv` during ZIP import (each diary row = +1 watch)
- Incremented by `rss_service.py` on rewatch detection (existing `is_watched` entry)
- Used in clustering weight multiplier (watch_count≥3 → ×1.5, watch_count=2 → ×1.2)
- Used in `_score_anchor_candidate` rewatch boost (up to ×1.4)
- `create_user_clusters` in upload.py receives `groq_client=groq_client` (not None)
  so LLM cluster labels are generated on initial upload.

---

## Security Rules

- Security headers required on all responses:
    X-Frame-Options: DENY
    X-Content-Type-Options: nosniff
    Referrer-Policy: strict-origin-when-cross-origin

- Backend dependencies hash-verified via requirements.lock
  Regenerate: pip-compile requirements.txt
              --generate-hashes -o requirements.lock

- Never accept user_id from request body/query on protected
  endpoints — always extract from JWT via get_current_user()

- Release age policy:

  Category              | Patch  | Minor  | Major
  ----------------------|--------|--------|-------
  Infrastructure/ML     | 30d    | 60d    | never
  Security deps         |  7d    | 21d    | never
  Dev tools             |  7d    | 14d    | never
  Frontend UI           |  7d    | 14d    | never
  Frontend core (Next)  | 14d    | 30d    | never

- Deferred major bumps (post-deployment only):
    fastapi, pandas, sentence-transformers, redis,
    bcrypt, groq, curl-cffi, eslint

---
