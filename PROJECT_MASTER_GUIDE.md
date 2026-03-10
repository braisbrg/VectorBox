# PROJECT MASTER GUIDE: VectorBox

> **Role:** Lead Software Architect Handover
> **Target Audience:** CTO / Senior Developers
> **Version:** 1.2.0 (Gold Master)
> **Last Updated:** 2026-03-05

This document serves as the absolute source of truth for the **VectorBox** (internal codename: *LetterboxRecommender* / *CineMatch*) project. It documents the existing state of the codebase, detailing architecture, algorithms, and logical flows.

---

## Quick Reference: Role-Specific Guides

For detailed rules and context, see the **Agentic Team Structure** in `.ai_context/employees/`:

| Role | Focus | File |
| :--- | :--- | :--- |
| **Architect** | Stack enforcement, forbidden patterns, async rules | [`.ai_context/employees/c-suite/architect.md`](.ai_context/employees/c-suite/architect.md) |
| **Frontend Director** | Next.js, Tailwind, Acid Design, Mobile UX | [`.ai_context/employees/directors/frontend.md`](.ai_context/employees/directors/frontend.md) |
| **Backend Director** | FastAPI, Postgres, Async patterns, Auth | [`.ai_context/employees/directors/backend.md`](.ai_context/employees/directors/backend.md) |
| **Data Science Director** | Trident, RRF, Sigmoid, Qdrant, Clustering | [`.ai_context/employees/directors/data-science.md`](.ai_context/employees/directors/data-science.md) |
| **DevOps Director** | Docker, Security, Scripts inventory | [`.ai_context/employees/directors/devops.md`](.ai_context/employees/directors/devops.md) |

> [!TIP]
> Use these role-specific files for detailed implementation guidance. This master guide provides the high-level overview.

---

## 1. The Technology Stack (The "Engine")

We use a modern, high-performance stack optimizing for **async concurrency** (Backend) and **visual fluidity** (Frontend).

### Frontend
- **Framework:** **Next.js 16.1.6** (App Router). Used for server-side rendering and static optimization.
- **Library:** **React 19.2.4**. Leveraging the latest concurrent features (Suspense, Transitions).
- **Styling:**
  - **Tailwind CSS 4.1.18**: **CSS-First Architecture**.
    - Configured via `@theme` in `global.css`.
    - No `tailwind.config.*` files.
    - Uses `@tailwindcss/postcss` plugin.
  - **Animations:** Pure CSS keyframes defined in `globals.css` (no `tailwindcss-animate`).
- **Animation:** **Framer Motion 12.34.0**. Powers all complex transitions, hover states, and carousel physics.
- **Utilities:** `tailwind-merge` v3 (Major version) + `clsx`.
- **Package Manager:** **pnpm**. Enforced via `STACK_RULES.md`.
- **Design System:** **Acid Design**. High-contrast neon aesthetics (`text-[#CCFF00]`, `bg-black/95`) with `Space Mono` typography.
- **Build System:** **Multi-Stage Docker Build**. Uses `output: 'standalone'` to minimize image size (~150MB).
- **Mobile First:** Fully responsive grid and touch-optimized navigation overlay.
- **UI UX/Effects:** Custom **"Tweak" System** (inspired by Magic UI / Aceternity concepts).
  - **Components:** `BorderBeam`, `SpotlightCard`, `ShimmerButton`, `GridPattern`.
  - **Error Handling:** Custom **Acid Design** 404/500 pages with glitch effects.
  - **Internationalization:** Full UI localization (EN/ES) via `next-intl` pattern.
  - **Location:** `frontend/components/tweak/`.

### Backend
- **Framework:** **FastAPI 0.122.0**. Chosen for native async support and high throughput.
- **Runtime:** **Python 3.11-slim**.
- **Server:** **Uvicorn** (Standard Worker).
- **ORM:** **SQLAlchemy 2.0.44 (Async)**. Uses `asyncpg` + `psycopg` drivers.
- **Validation:** **Pydantic V2**. Strongly typed data models for all inputs/outputs, including external responses (OMDb, Qdrant) via `external_schemas.py`.
- **Scraper:** **curl_cffi 0.7.4**. Impersonates Chrome 120 for evasion.
- **AI/ML Layer:**
  - **Groq:** Primary and only Provider.
    - **Tier 1 (Speed):** `meta-llama/llama-4-scout-17b-16e-instruct` (Search Bar).
    - **Tier 2 (Intelligence):** `llama-3.3-70b-versatile` ("Deep Analysis" & Retry).
    - **Tier 3 (Fallback):** `openai/gpt-oss-120b` (Groq fallback).
  - **Instructor:** Python library to force structured JSON outputs from LLMs. (Strictly utilizes `AsyncOpenAI` clients to prevent event loop blocking).
  - **Sentence-Transformers:** Local inference using `all-MiniLM-L6-v2` (**CPU Optimized**) for embedding generation. Singleton Pattern enforced.

### Service Instantiation (Dependency Injection)
- **Global HTTP Client:** The FastAPI `lifespan` initializes a single global `httpx.AsyncClient` attached to `app.state` to leverage connection pooling effectively across all external requests.
- **Rule:** Heavy clients (`Qdrant`, `TMDB`, `EmbeddingModel`, `OMDbClient`) **MUST** be Singletons injected via `dependencies.py` (e.g., `Depends(get_tmdb_client)`).
- **Requirement:** When instantiating service classes (like `RecommendationService`) within endpoints or parallel tasks, you **MUST** pass the injected dependencies down the chain.
- **Ban:** Do NOT instantiate new instances of `TMDBClient()`, `OMDbClient()`, or `httpx.AsyncClient()` inside service class `__init__` methods. This creates catastrophic HTTPX connection leaks during parallel execution (like `asyncio.gather`).
- **Resilience:** `TMDBClient` implements Exponential Backoff retries for 429 Rate Limit errors.

### Data Layer
- **Relational DB:** **PostgreSQL 15-alpine**. Stores User profiles, Ratings, Watchlists, Movie metadata, and Clusters.
  - **Optimization:** Heavy use of Indexes (`vectorbox_score`, `popularity`, `vote_count`).
- **Vector DB:** **Qdrant** (Latest Docker Image). Stores 384-dimensional dense vectors for semantic search.
  - **Optimization:** Payload Indexes on `genres`, `year`, and `score` for fast filtering.
- **Cache:** **Redis 7-alpine**. Used for:
  - Caching the entire Master Feed JSON response (`FeedResponse`) for 1 hour to provide sub-100ms load times.
  - Caching TMDB/Provider API responses (`provider_service`, `tmdb_client`).
  - Session/Rate limiting.

### Infrastructure
- **Containerization:** **Docker Compose**. Orchestrates `frontend`, `backend`, `postgres`, `qdrant`, `redis`, and `jaeger`.
- **Optimization:** **Multi-Stage Builds** (Frontend) for minimal image size and **PyTorch CPU-only** (Backend) for reduced memory footprint.
- **Observability:** **OpenTelemetry + Jaeger**. Full distributed tracing across all services.
  - **Exporter:** OTLP/gRPC → Jaeger All-in-One (`jaegertracing/all-in-one`).
  - **Auto-Instrumented:** FastAPI (HTTP spans), SQLAlchemy (DB query spans), Redis (cache spans).
  - **Custom Spans:** Trident Engine signals (A, Auteur, C) each produce named spans with `user_id` and `result_count` attributes.
  - **Jaeger UI:** `http://localhost:16686` — search by service `vectorbox-backend`.
- **Security Tools:**
  - **Husky & Lint-Staged:** Pre-commit hooks for code quality (ESLint v9 pinned).
  - **pip-audit:** Scans Python dependencies for CVEs during build using hash-verified `requirements.lock`.
  - **pnpm audit:** Scans JS dependencies.
- **Validation & QA:**
  - **Automated E2E Suite:** Playwright-based QA suite (`frontend/e2e/`) checking Auth, Mobile UX, and Error States.
  - **Chaos Monkey & Whitelist Testing:** `verify_nlp_fallback.py` and `test_es_whitelist.py` ensure LLM failovers trigger correctly.
  - **QA Certification protocol:** The full E2E run is certified in `docs/QA_RUNBOOK.md`.
  - **Frontend Quality Audit:** Strictly adheres to Addy Osmani standards for Core Web Vitals (LCP/INP/CLS), WCAG 2.1 AA Accessibility, and Modern Best Practices.

### Authentication (v1.2)
- **Model:** Netflix-style profiles with **Username + 4-digit PIN**.
- **Hashing:** **passlib[bcrypt]** for PIN hashing.
- **Sessions:** Long-lived **secret_token (UUID)** stored in cookies.
- **Letterboxd Linking:** Decoupled from VectorBox username. Users link their Letterboxd profile separately.

---

## 2. Feature Catalogue (The "What")

### A. The Feed (Home Page)
The feed is composed of multiple "Sections", generated largely in parallel by `FeedService` using **Batch Fetching** to eliminate N+1 queries.

| Section | Logic / Source |
| :--- | :--- |
| **Available Now** | **Priority Row.** Fetches user's *unwatched* Watchlist items that are currently streaming on their active providers (Netflix, etc.). |
| **Popular on Letterboxd** | Fetches trending movies from TMDB/Letterboxd (cached via `ThreadingService`). |
| **Because you watched [X]** | **Item-Item Collaborative Filtering.** Picks a highly-rated or liked movie from user history, generates a *content-only* vector (ignoring title), and finds similar vectors in Qdrant. Deduplicated into a single `_item_to_item_search()` helper in `search.py`. |
| **Your Taste ([Cluster])** | **Centroid Search.** Picks one of the user's "Taste Clusters" (e.g., "80s Sci-Fi"), computes the centroid of that cluster, and searches Qdrant. |
| **Hidden Gems** | **Score-to-Hype Filtering (v1.2).** Searches Qdrant near user's global profile centroid with **dynamic thresholds** based on user's movie count: Cold start (<30 movies) uses `score > 60, popularity < 40, votes > 200`; Growing (30–99) uses `score > 65, popularity < 30, votes > 300`; Rich (100+) uses `score > 75, popularity < 20, votes > 500`. Identifies critically acclaimed but underexposed films. |
| **Deep Dive** | **Pure Item-Based.** Uses the weighted "Super Seed" logic (see Algorithms) to find movies similar to the user's favorites. Now runs fully in PARALLEL with the other feed tasks for maximum performance. |
| **Comfort Zone (Wildcard)** | **Anti-Recommendation.** Finds highly-rated movies whose genres *do not overlap* with the user's dominant cluster genres. |
| **Random Picks** | Random selection from top 500 "VectorBox Scored" movies in DB. |

### B. The "Magic Box" (NLP Search)
A natural language search interface powered by `nlp_search.py` with a 3-Tier Cascading Fallback Architecture.
1.  **Input:** User types "gangster movies from the 90s that are dark".
2.  **Interpretation (LLM):** The robust LLM pipeline (Llama 4 Scout -> 70B -> 120B) via `Instructor` parses this into a `MovieSearchIntent` object:
    -   `semantic_query`: "organized crime, mafia, noir, crime drama, 1990s" (Expanded synonyms).
    -   `year_min`: 1990, `year_max`: 1999.
    -   `include_genres`: ["Crime", "Drama"].
    -   `popularity_vibe`: "any".
3.  **Execution:** `QdrantService` executes a vector search with the expanded query, filtering strictly by year and genre.

### C. Tools
- **Group Sync:** (`rss_service.get_group_recommendations_hybrid`) calculates a "Group Vibe" by taking the vectors of multiple users (some from DB, some guests via RSS), finding a centroid, and scoring movies based on *Max Similarity* (rewarding passion) while penalizing movies that *any* single user hates (Similarity < 0.65).
- **Random Picker:** Selects a random movie from a filtered list of recommendations.

### D. Sync System
- **RSS Sync:** `RSSService` pulls `letterboxd.com/{letterboxd_username}/rss`. Uses linked Letterboxd profile (v1.1).
- **Phantom Check:** Detects if a movie on a given `watched_date` in DB matches the RSS feed. If not (and it's a "phantom" duplicate often caused by bad TMDB matching), it deletes it.
- **Match Strategy:** 1. TMDB ID (if present in RSS) -> 2. Letterboxd URI -> 3. Title + Year match.

### E. Background Tasks & Progress (v1.1)
- **TaskStore:** Redis-based progress tracking for long-running operations.
- **Endpoints:** `POST /upload/export` returns `task_id`, `GET /tasks/{task_id}` polls progress.
- **Frontend:** `ProgressModal` component polls and displays real-time progress with step descriptions ("Enriching", "Clustering"). Replaces static success messages.
- **Enrichment (v1.2):** `enrich_movie` self-healing logic now runs as a **Background Task** (FastAPI) to prevent blocking the main thread during recommendation generation.
  - **Rule:** Background tasks MUST own their own `AsyncSession`. NEVER reuse the request-scoped `db` session, as it is torn down when the HTTP response returns (causing `MissingGreenlet` errors).

---

## 3. The "Brain" of VectorBox (The Algorithms)

### The "Trident" Hybrid System (Fully Operational)
VectorBox generates recommendations using three distinct engines fused via **Reciprocal Rank Fusion (RRF)**:
1.  **Signal A: Vector (Vibe):** Qdrant search using dense embeddings. Captures "Vibe" and plot similarity. Seeds from movies rated 4+ stars **or** explicitly liked (`is_liked`).
2.  **Signal Auteur (Directors):** Boosts movies by directors the user loves (explicit check against user's high-rated history).
3.  **Signal C: Hidden Gems:** Score-to-Hype ratio filtering with **dynamic thresholds** that adapt to user's movie count (see Feed section table). Identifies critically acclaimed but underexposed films.

### The Scoring Formula
Every recommendation gets a final score (0-100%) derived from:
```python
FinalScore = Similarity (Cosine) * QualityWeight (Sigmoid)
```
- **Similarity:** Raw cosine similarity from Qdrant (0.0 - 1.0).
- **Quality Weight (Sigmoid):** A non-linear curve applied to the *VectorBox Score* (0-100).
    - **Formula:** `1 / (1 + e^(-0.15 * (Score - 65)))`
    - **Effect:** Movies with a score > 65 get a boost. Movies < 50 get a heavy penalty. This prevents "relevant trash" from appearing.

### Diversity: MMR & Collection Collapsing
- **MMR (Maximal Marginal Relevance):** Used in `clustering_service.mmr_rerank` (`lambda=0.7`). It re-ranks the top results to penalize items that are too similar to items *already selected* for the list.
- **Collection Collapsing:** In `get_item_based_recommendations`, multiple movies from the same franchise (e.g., *Harry Potter 1, 2, 3*) are collapsed into a single "Super Seed" (the highest-rated one). This prevents a single franchise from flooding the recommendation inputs.

### Clustering Logic (`ClusteringService`)
- **Algorithm:** K-Means.
- **Vectors:** `all-MiniLM-L6-v2` (384d).
- **Weights:** Movies rated 4+ stars get `1.0` weight. 2-3.5 stars get `0.5`. Others `0.1`.
- **Recency Bias:** Older ratings decay in weight if `use_recency_bias=True`.
- **Optimal Clusters:** `min(5, max(2, total_movies // 20))`. Dynamic based on history size.

---

## 4. Data Architecture & Flow

### Ingestion Pipeline
1.  **Trigger:** `seed_db.py`, `RSS Sync`, or `Auto-Ingest` (when Qdrant returns a "Ghost" ID not in DB).
2.  **TMDB API:** `TMDBClient` fetches metadata, credits, keywords, and release dates. **Singleton Instance** prevents connection exhaustion.
3.  **Vector Generation:** `EmbeddingService` creates a synthetic text chunk: `Title + Overview + Genres + Keywords`. This chunk is embedded locally via `SentenceTransformer`.
4.  **Storage:**
    -   **Postgres:** Stores metadata in `movies` table. Uses atomic `ON CONFLICT DO UPDATE` UPSERTs to prevent Time-Of-Check-To-Time-Of-Use (TOCTOU) race conditions.
    -   **Qdrant:** Stores vector with payload (TMDB ID, Genres, Year). Batch upserts check for existing payload differences via a quick `scroll` operation to skip redundant I/O writes.

### Schema & ID Type Key Points
- **Rule of Thumb:** VectorBox uses TWO different movie ID spaces. Mixing them causes silent deduplication failures.
  - `internal_id` → `Movie.id` (PostgreSQL auto-increment)
  - `tmdb_id` → `Movie.tmdb_id` (TMDB API identifier)
  - `seen_ids` set (feed deduplication) MUST use `tmdb_id`.
  - `watched_ids` set MUST use `internal_id`.
- **`movies` table:** Stores `vectorbox_score`, extensive ratings (IMDb, Metacritic, TMDB), and localized `overview_es`.
- **`user_ratings` table:** Links Users to Movies. Contains `is_watched`, `is_watchlist`, `rating`, `watched_date`. `movie_id` is an FK to `Movie.id` (internal).
- **`user_clusters` table:** Stores the computed K-Means centroids and labels for each user.

### Caching Strategy
- **Redis (Master Feed Cache):** Caches the completely assembled Main Feed JSON response for 1 hour per user/region to provide sub-100ms load times.
- **ProviderService:** Caches availability (Netflix/Prime) to avoid hitting TMDB API limit.
- **Invalidation:** `reset_profiles.py` and `ClusteringService` have logic to wipe cache keys when user data changes significantly.

---

## 5. Security & Safety Protocols (Implemented)

### IDOR & Access Control (No-Trust Policy)
- **Rule:** `user_id` is **NEVER** accepted from the client as a query/path parameter for protected resources.
- **Mechanism:** Identity is strictly derived from the `vectorbox_token` session cookie via `dependencies.get_current_user`.
- **Enforcement:** All sensitive endpoints (Uploads, Recommendations, User Data) use `verify_user_ownership` or implicit checking.

### Session Management
- **Token:** UUIDv4 `vectorbox_token`.
- **Rotation:** Session tokens are rotated strictly upon login/logout to prevent fixation.
- **CSRF:** `SameSite=Lax` cookie attribute prevents Cross-Site Request Forgery.
- **HttpOnly:** Prevents XSS token theft.

### Supply Chain Security
- **Release Age Policy:** Differentiated cooldown rules applied via `.github/dependabot.yml` and `backend/pip.conf` (global pip minimum-release-age: 720h / 30 days).
- **Frontend Safety:** `frontend/.npmrc` enforces `frozen-lockfile=true` and `audit=true` to prevent unauthorized package mutations. Local installs must use `pnpm install --no-frozen-lockfile`.
- **Audits:**
  - `audit_backend.ps1` runs `pip-audit --strict`.
  - Frontend `package.json` runs `pnpm audit`.

### Privacy
- **Email Dropping:** The `CSVParser` creates movie dictionaries containing *only* `{Title, Year, URI, Rating, Date}`. It ignores/drops the `Email` column from Letterboxd exports by virtue of not including it in the output schema.

### Container Hardening
- **User:** Backend container runs as non-root user `vectorbox` (UID 1000).
- **Network Ports:** Databases (Postgres 5432, Qdrant 6333, Redis 6379, Jaeger 16686) are strictly bound to `127.0.0.1` locally via `docker-compose.yml` to prevent public internet access.
- **Production Overrides:** `docker-compose.prod.yml` enforces fail-fast checking of required environment variables for production security.

### HTTP Security Headers
The frontend (`next.config.js`) enforces:
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`

### Frontend Performance & Hardening
- **Core Web Vitals:** Strict LCP priority loading, direct DOM mutation for continuous interactions (INP), and `display: "optional"` for Custom Fonts (CLS).
- **Accessibility:** Keyboard event trapping, ARIA Live regions for dynamic search, and strict contrast ratios (`text-zinc-400` on black).
- **Resilience:** `AbortController` timeouts on all SSR fetch calls.
- **Error Boundaries:** The custom `<AcidError />` component intercepts API failures (like `503` from Deep Health Checks or `DATA_STREAM_INTERRUPTED`) providing a highly styled, user-friendly recovery UI instead of crashing the React tree. Fully accessible with `role="alert"`, `aria-live="assertive"`, and `aria-atomic="true"`. Includes a `glitch` keyframe animation defined in `globals.css`.

---

## 6. Directory Structure

### Backend (`/backend`)
- **`routers/`**: API Endpoints (`recommendations.py`, `search.py`, `auth.py`).
- **`services/`**: Business Logic.
  - `feed_service.py`: Orchestrates the Home Feed, handling parallel execution.
  - `recommendation_engine.py`: Defines the strategies and algorithms (Hidden Gems, etc.) used by the Feed.
  - `movie_factory.py`: Centralizes ingestion pipeline (TMDB, OMDb, Qdrant payload generation).
  - `clustering_service.py`: Core logic for K-Means, MMR, and recommendation rankings.
  - `nlp_search.py`: "Magic Box" LLM logic using Groq+Instructor.
  - `rss_service.py`: Sync logic for Letterboxd RSS feeds.
- **`models/`**: SQLAlchemy (`database.py`) and Pydantic (`schemas.py`, `external_schemas.py`) definitions.
- **`scripts/`**: Maintenance tasks (`seed_db.py`, `enrich_vectors.py`, `popular_scraper.py`, `reset_profiles.py`, `test_magic_box.py`, `verify_feed_parallelism.py`, `test_idor_hidden_gems.py`, `test_trident_math.py`).

### Frontend (`/frontend`)
- **`app/`**: Next.js App Router pages (`page.tsx` for feed).
- **`components/`**: React components.
  - `magic-search.tsx`: The UI for the NLP search bar.
  - `feed-container.tsx`: The main scrollable feed.
  - `recommendation-grid.tsx`: Displays movie cards.
- **`ui/`**: Reusable primitives settings (buttons, dialogs).

---
## 7. Disaster Recovery & Maintenance

### Snapshot & Backup Strategy
We implement a "Snapshot & Rotation" strategy to ensure data resilience:
- **Frequency:** Ad-hoc (Manual trigger via wrappers) or scheduled via host cron.
- **Components:**
  1.  **Qdrant:** Uses the Snapshot API (`POST /collections/{name}/snapshots`) to capture vector shards.
  2.  **Postgres:** Uses `pg_dump` to capture relational schema and data.
  3.  **Redis:** Triggers a `BGSAVE` to asynchronously dump session and cached keys to `dump.rdb`.
- **Rotation:** The `backup_manager.py` script automatically enforces a policy to keep only the **last 5 backups** to save disk space on resource-constrained hosts.

### Restoration
- **Script:** `restore_manager.py` takes an archived ZIP and automatically orchestrates the destructive restoration process (terminates Postgres connections, drops Qdrant collections, restarts Redis).
- **Dry-Run:** Supports `--dry-run` to preview operations before execution.

### Persistence Architecture
- **Volume Mapping:** To prevent data loss during container recreation, backups are written to `/app/backups` inside the container but mapped to the Host machine at `./backups`.
- **Mapping:** `host:./backups` <-> `container:/app/backups`.

---

### 8. Observability & Tracing

### Architecture
VectorBox uses **OpenTelemetry SDK** with a **Jaeger All-in-One** backend for distributed tracing. Every request produces a nested timeline visible in the Jaeger UI.

### Instrumentation Layers
| Layer | Instrumentation | Spans Generated |
| :--- | :--- | :--- |
| **HTTP** | `FastAPIInstrumentor` | One span per API request |
| **Database** | `SQLAlchemyInstrumentor` | One span per SQL query |
| **Cache** | `RedisInstrumentor` | One span per Redis command |
| **Trident A** | Custom (`telemetry.py`) | `trident.signal_a.because_you_watched` |
| **Trident B** | Custom (`telemetry.py`) | `trident.signal_b.your_taste` |
| **Trident C** | Custom (`telemetry.py`) | `trident.signal_c.hidden_gems` |

### Deep Observability (v1.2+)
- **Signal Timing:** The `RecommendationService` logs sub-millisecond execution times for each Trident signal with the `[TRIDENT]` prefix.
- **Circuit Breakers:** `TMDBClient` utilizes circuit breakers for rate limit protection, logging `CRITICAL` alerts if the breaker trips.
- **Deep Health Checks:** `/health` endpoint performs active ping checks on Postgres, Redis, and Qdrant, returning `503 Service Unavailable` with specific failure details if any dependency is down.

### Span Attributes
All Trident spans include: `user_id`, `country`, `result_count`. Signal A also includes `anchor_movie`. Signal B includes `cluster_id`.

### Access
- **Jaeger UI:** `http://localhost:16686`
- **Service Name:** `vectorbox-backend`
- **Config:** `telemetry.py` — reads `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_SERVICE_NAME` from env.

---

**Last Updated:** 2026-03-05 (Gold Master / QA Certified)
**Maintained By:** VectorBox Team
