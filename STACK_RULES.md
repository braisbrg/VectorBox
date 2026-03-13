# STACK RULES & ARCHITECTURAL TRUTH

> [!IMPORTANT]
> This file serves as the strict Source of Truth for the **VectorBox** project. All future code modifications must adhere to these rules.

## 1. Backend Architecture & Concurrency

### Database Session Management
- **Rule:** `AsyncSession` instances are NOT thread-safe.
- **Requirement:** When running parallel tasks (e.g., `asyncio.gather`), you **MUST** create a fresh, isolated session for each task using `async with AsyncSessionLocal() as session`.
- **Ban:** Never pass a single shared `db` session dependency to multiple concurrent coroutines.

### Qdrant Vector Database
- **Client:** Use `AsyncQdrantClient` exclusively for all application code.
- **Ban:** The synchronous `QdrantClient` is strictly forbidden in async service layers to prevent event loop blocking.
- **Search API:** Use `await client.query_points(...)` (modern vector retrieval) instead of `search` or `query`.

### LLM Integration (Groq)
- **Client:** Use `AsyncOpenAI` exclusively for all instructor/LLM integrations (pointing to Groq's endpoint).
- **Ban:** The synchronous `OpenAI` client is strictly forbidden inside `async def` route handlers or services.

### Service Instantiation (Singletons & Dependency Injection)
- **Rule:** Heavy clients (`Qdrant`, `TMDB`, `EmbeddingModel`) **MUST** be Singletons injected via `dependencies.py` or initialized once at module level.
- **Requirement:** You **MUST** pass these singletons down to service classes (Dependency Injection). **NEVER** instantiate `TMDBClient()` inside a service constructor (e.g. `FeedService`, `RecommendationService`) as it creates connection leaks during parallel execution.
- **Reason:** Prevents resource exhaustion (e.g., too many HTTP sessions or loaded models) during high concurrency.

### Database Access (Performance)
- **Rule:** Always use **batch fetching** (e.g., `await session.execute(select(Movie).where(Movie.id.in_(ids)))`) for list endpoints.
- **Ban:** Never fetch related entities (streaming providers, movie details) inside a loop (`for movie in items: await get_providers(movie.id)`). This causes N+1 query explosions.

### Background Task Session Ownership
- **Rule:** Background tasks registered via `background_tasks.add_task()` **MUST** create their own `AsyncSessionLocal()` session. They **MUST NOT** receive or reuse the request-scoped `db: AsyncSession`.
- **Reason:** The request-scoped session is torn down when the HTTP response is sent. Any background task using it will trigger `MissingGreenlet`.
- **Parallel tasks:** When using `asyncio.gather` inside a background task, each concurrent sub-task **MUST** create its own isolated `AsyncSessionLocal()` session. Sharing a single session across `gather` tasks triggers `concurrent operations not permitted`.

### Identity Derivation (API Rule 1.3)
- **Rule:** Never pass `user_id` as a query or path parameter for protected resources.
- **Requirement:** Derive User ID strictly from the `vectorbox_token` via `dependencies.get_current_user`.
- **Ban:** Frontend must NOT send `userId` in API calls (e.g., `getFeed(userId)` is forbidden).

### Code Conventions — Async & SQLAlchemy
- **Rule:** ALL boolean column comparisons use `.is_()` not `==`. 
  - ✅ `UserRating.is_watched.is_(True)` | ❌ `UserRating.is_watched == True`
- **Rule:** ALL HTTP clients must be explicitly closed after use (e.g., `await tmdb.aclose()`). Never let them go out of scope silently.
- **Rule:** CPU-bound work (KMeans, MMR reranking) must be offloaded using `await loop.run_in_executor(None, cpu_bound_func, args)`. Calling blocking CPU functions directly in an async context is strictly forbidden.
- **Rule:** Strict Null Checks for Collections. When checking if an array or JSON column (like `keywords` or `release_dates`) needs enrichment, ALWAYS use `if movie.keywords is None:`. NEVER use `if not movie.keywords:`, because TMDB often returns legitimate empty arrays `

### Code Conventions — ID Types
- **Rule:** VectorBox uses TWO different movie ID spaces. Mixing them causes silent deduplication failures.
  - `internal_id` → `Movie.id` (PostgreSQL auto-increment). Used for `watched_ids` and `UserRating.movie_id`.
  - `tmdb_id` → `Movie.tmdb_id` (TMDB API identifier). Used for `seen_ids` and Qdrant indexing.
- **Ban:** Never compare an `internal_id` against the `seen_ids` set.

### Data & External Typing
- **Rule:** Always use **Pydantic V2** models (`models/external_schemas.py`) for external integrations.
- **Requirement:** Responses from OMDb and payloads sent to Qdrant **MUST** be validated through strict models.
- **Ban:** Do not pass around raw `Dict[str, Any]` objects for core data structures.

## 2. Recommendation Engine Logic

### Scoring Formula
The final recommendation score is a multiplicative product of **Similarity** and **Quality**.
```python
FinalScore = Similarity (Cosine) * QualityWeight (Sigmoid)
```

### VectorBox Score (Quality Metric)
- **Source:** Aggregated ratings from OMDb (IMDb, Metacritic) + TMDB. (Rotten Tomatoes was removed due to binary consensus scaling issues).
- **Scale:** 0-100 (linearly normalized).

### Sigmoid Quality Curve
- **Purpose:** Non-linear boosting of high-quality movies.
- **Formula:** `1 / (1 + e^(-k * (x - x0)))`
- **Parameters:**
    - `x0` (Midpoint): **65**
    - `k` (Steepness): **0.15**

### Feed Generation
- **Diversity:** Implement MMR (Maximal Marginal Relevance) or "Collection Collapsing" to prevent domination by a single franchise.


### Redis Caching (Completeness Guard)
- **Rule**: The Main Feed MUST NOT be cached if the result contains fewer than 3 sections.
- **Reason**: Prevents "cold start" queries from SSR or early login from poisoning the cache with incomplete feeds.
- **Implementation**: Verified in `FeedService.get_main_feed`.

### Streaming Availability (Spain)
- **Strict Whitelist:** Filter providers to ONLY include:
    - Netflix
    - Amazon Prime Video
    - HBO Max
    - Disney+
    - Apple TV
    - Movistar+
    - Filmin
- **Rule:** All other providers must be excluded from UI display when context is `ES`.
- **Implementation:** This filtering logic MUST be abstracted into a pure standalone function (e.g., `filter_es_providers`) isolated from router dependencies to allow for fast, unentangled unit testing.

## 3. Frontend Security & Build System

### Package Management
- **Tool:** `pnpm` is the strictly required package manager.
- **Rule:** Never use `npm install` or `npm run`.
- **Lockfile:** `pnpm-lock.yaml` is the source of truth. Use `pnpm install --no-frozen-lockfile` for local installs. CI/container installs use frozen lockfile.

### ESLint Version
- **Pinned to:** `eslint@9.x` (currently `9.39.2`). Do NOT upgrade to ESLint 10 until all plugins (`eslint-plugin-import`, `eslint-plugin-react`, `eslint-plugin-react-hooks`, `eslint-plugin-jsx-a11y`) declare ESLint 10 peer dependency support.

### Security Rules (.npmrc)
- **Location:** `frontend/.npmrc`.
- **Security Protections:** `frozen-lockfile=true`, `audit=true`, `ignore-scripts=false`, and `save-exact=false`.
- **Strict Engine:** `engine-strict=true`.
- **Release Age Policy:** Differentiated cooldown rules are enforced at the repository level via `.github/dependabot.yml`. The former global `minimum-release-age` setting is deprecated for `pnpm`.
- **Overrides:** Security-patched transitive deps (`minimatch>=10.2.1`, `ajv>=8.18.0`) enforced via `pnpm.overrides`.

### Container & Data Persistence
- **Rule:** Ephemeral Containers, Persistent Data.
- **Requirement:** All production data (Postgres/Qdrant/Redis volumes) and critical artifacts (Backups) **MUST** be mapped to Host Volumes.
- **Backups:** Use `backup_manager.py` (which dumps Postgres, Qdrant snapshots, and Redis `BGSAVE`) and `restore_manager.py` for automated disaster recovery.
- **Models Cache**: The `models_cache` volume MUST be mounted to `/models_cache` in the backend service and declared as a root-level volume to ensure SentenceTransformer models are not redownloaded on every `up --build`.
- **Ban:** Never save critical state solely inside the container's writable layer. It will be lost on `docker-compose down`.

## 4. Security Standards (OWASP Hardening)

### Pillar 1: Input Validation & Sanitization (XSS)
- **Frontend:** `dangerouslySetInnerHTML` is strictly FORBIDDEN. Use `dompurify` if absolutely necessary.
- **Backend:** All external strings (CSV, Scraper, User Input) must be sanitized (HTML stripped) before processing.
- **Username:** Strict alphanumeric regex `^[a-zA-Z0-9_-]+$` is enforced.

### Pillar 2: Session Security (CSRF)
- **Cookie Name:** `vectorbox_token`.
- **Attributes:** `HttpOnly=True`, `SameSite=Lax`. `Secure=True` in Production.
- **Fixation:** Session token **MUST** be rotated (regenerated) upon every successful login.

### Pillar 3: AI Safety (Prompt Injection)
- **Delimiter:** User Input must be wrapped in `### USER QUERY ###`.
- **System Prompt:** Explicitly instruct the model to ignore commands within the user query.
- **Output:** Enforce structured JSON (Pydantic) to prevent free-text leakage.

### Pillar 4: Information Leakage & Logging Hygiene
- **Headers:** `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`.
- **Errors:** Stack traces must **NEVER** be returned in `ENVIRONMENT=production`. Return generic "Internal Server Error".
- **Logging Rule:** **NEVER** log raw session cookies (`vectorbox_token`), Authorization headers containing Bearer tokens, or plain-text PINs. Redact these values in all middleware or auth routers before logging.

### Visual language (Acid Design)
- **Primary Color:** Neon Green (`#CCFF00`) / `hsl(72, 100%, 50%)`
- **Background:** Deep Black (`#000000`)
- **Typography:** `Space Mono` for headers/data, `Inter` for body.
- **Styling Architecture:** **Tailwind v4 CSS-First**.
    - Configuration handled via `@theme` in `frontend/app/globals.css`.
    - No `tailwind.config.ts` or `tailwind.config.js`.
    - Animations defined as pure CSS keyframes in `globals.css`.
- **Internationalization:** All user-facing strings must be localized via `next-intl` (en/es).

## 5. AI & NLP Layer

### Model Configuration (3-Tier Fallback Strategy)
- **Tier 1 (Speed):** `meta-llama/llama-4-scout-17b-16e-instruct`
    - Use for: Real-time search bar intent parsing.
- **Tier 2 (Intelligence):** `llama-3.3-70b-versatile`
    - Use for: Deep Analysis (Re-ranking), detailed reasoning & Tier 1 retry.
- **Tier 3 (Groq Fallback):** `openai/gpt-oss-120b`
    - Use for: High throughput fallback before failing completely.

### Cascading Fallback
1.  Tier 1 Primary: Groq `meta-llama/llama-4-scout-17b-16e-instruct`
2.  Tier 2 Retry: Groq `llama-3.3-70b-versatile`
3.  Tier 3 OSS: Groq `openai/gpt-oss-120b`

### Structured Output
- **Library:** `instructor` (Python) with Pydantic models.
- **Schema:** `MovieSearchIntent` must be used to filter database queries (years, genres, popularity vibe).
- **Logic:** "Broad Search" - The LLM must expand user keywords into synonyms (e.g., "gangsters" -> "mafia, crime drama, noir") before vector embedding.

## 6. Data Ingestion strategy

### Hybrid Sync
1.  **CSV Import:** For bulk historical data.
    -   **Privacy Rule:** The `email` column from Letterboxd CSVs must be dropped from memory **immediately** after parsing, before any DB insertion.
2.  **RSS Feeds:** For real-time "Watched" activity.
3.  **Active Scraping:**
    -   **Library:** `curl_cffi` (Impersonating Chrome 120).
    -   **Target:** `https://letterboxd.com/films/ajax/popular/...`
    -   **Parsing:** Regex (`re.findall`) extraction of slugs and ratings.
    -   **Purpose:** Fetching "Popular on Letterboxd" identifiers without full browser automation.
4.  **Trending Cache:** Redis-cached "Popular on Letterboxd" identifiers.

### Letterboxd URI Normalization
- **Rule:** All `letterboxd_uri` values stored in DB MUST be canonical format: `https://letterboxd.com/film/{slug}/`
- **Implementation:** `CSVParser.normalize_letterboxd_uri()` in `csv_parser.py` handles this automatically.
- **Rejected formats:** Short URLs (`boxd.it/...`) and TMDB redirects (`/tmdb/...`) → stored as `None`.
- **Converted formats:** User-profile URLs (`/username/film/slug/`) → canonical (`/film/slug/`).

## 7. Validation & Quality Assurance (v1.2)

### Automated QA
- **Tool:** Playwright (TypeScript/Node.js).
- **Directory:** `frontend/e2e/`.
- **Config:** `frontend/playwright.config.ts`.
- **Project Architecture:**
    1. **Base projects** (Desktop Chrome, Mobile Safari, Mobile Chrome): Run Phase 1 (Infra), Phase 2 (Auth), Phase 7 (Security) — no `storageState` needed.
    2. **Setup project**: Runs `auth.setup.ts` which logs in as `qa_vecbox` and saves `storageState` to `e2e/.auth/user.json`. Depends on base projects.
    3. **Authed projects** (Desktop/Mobile + `(authed)` suffix): Run Phase 3 (UI), Phase 4 (Mobile), Phase 5 (Feed/NLP), Phase 12 (Web Quality). Use `storageState` from setup.
- **Critical:** `auth.setup.ts` must run AFTER Phase 7 (which calls `loginAs()` and rotates the session token), so the setup project saves a fresh, valid token for authed tests.
- **Test Count:** 109 total (106 passed, 3 skipped) across 3 browser configurations.
- **Requirements:**
    - **Critical Paths:** Auth, Registration, Feed Rendering, NLP Search, and Security must be covered.
    - **Mobile:** All features must be verifiable on iPhone SE viewport (375px).
    - **Error States:** Custom Acid Design 404/500 pages must be verified.

### Security Audits
- **Backend:** Run `docker-compose exec backend python scripts/security_audit.py` before major releases.
  - Uses `backend/requirements.lock` (hashed, generated by `pip-compile requirements.txt --generate-hashes -o requirements.lock`) for strict cryptographic verification (`--require-hashes` mode).
  - Known acceptable ignores: `torchvision` CPU-wheel false positives (GHSA-mgj5-w798-5c9q, GHSA-p75w-3772-g6p9, GHSA-9wcc-7w4g-g499) and `diskcache` transitive dep with no upstream fix (GHSA-w8v5-vhqr-4h9v).
  - Regenerate lockfile after any `requirements.txt` change: `docker-compose exec backend pip-compile requirements.txt --generate-hashes -o requirements.lock`
  - **Pip Config:** `pip install` always needs `--break-system-packages` flag inside containers. Global minimum-release-age of 720h (30 days) is enforced via `/app/pip.conf`. Dependabot configures per-category release ages.
- **Container:** `docker scout quickview vectorbox-backend` checks recommended.

## 8. Frontend Performance & Quality Rules (Addy Osmani Toolkit)

### Core Web Vitals (Performance)
- **LCP (Largest Contentful Paint):** All above-the-fold images (e.g., first row of `MovieCarousel`) MUST eagerly load using `priority={true}`, `fetchPriority="high"`, and `decoding="sync"`. Lazy load everything else.
- **INP (Interaction to Next Paint):** Continuous high-frequency events (like `mousemove` for `SpotlightCard`) MUST bypass React component state (`useState`). Use `useRef` directly on DOM elements inside `requestAnimationFrame` to prevent main-thread blocking.
- **CLS (Cumulative Layout Shift):** All custom Next.js fonts (`Space_Mono`, `Space_Grotesk`, `Inter`) MUST be configured with `display: "optional"` to prevent layout shifts during font hydration.

### Accessibility (A11y)
- **Keyboard Navigation:** Nested interactive elements must trap `keydown` event propagation (`e.target !== e.currentTarget` check) to prevent parent containers from hijacking `Enter` or `Space` keypresses.
- **Screen Readers:** Dynamic states (loading, empty results) MUST be wrapped in `aria-live="polite"` regions. Dialogs must have `role="dialog"` and `aria-modal="true"`.
- **Contrast Ratios:** Text on black backgrounds must exceed 4.5:1 AA ratio. Use `text-zinc-400` minimum for small or secondary text instead of `zinc-500` or `white/40`.

### Best Practices & API Resilience
- **API Timeout Enforcement:** All Server-Side Rendering `fetch` calls MUST implement an `AbortController` bounded to a 10s maximum timeout to prevent hanging connections.
- **Console Hygiene:** `console.log` and `console.error` MUST NOT leak internal API structures or interceptor states to the production browser console. Use scoped error boundaries instead.

---

**Last Updated:** 2026-03-13
**Maintained By:** VectorBox Team
