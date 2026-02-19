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

### Service Instantiation (Singletons)
- **Rule:** Heavy clients (`Qdrant`, `TMDB`, `EmbeddingModel`) **MUST** be Singletons injected via `dependencies.py` or initialized once at module level.
- **Reason:** Prevents resource exhaustion (e.g., too many HTTP sessions or loaded models) during high concurrency.

### Database Access (Performance)
- **Rule:** Always use **batch fetching** (e.g., `await session.execute(select(Movie).where(Movie.id.in_(ids)))`) for list endpoints.
- **Ban:** Never fetch related entities (streaming providers, movie details) inside a loop (`for movie in items: await get_providers(movie.id)`). This causes N+1 query explosions.

### Identity Derivation (API Rule 1.3)
- **Rule:** Never pass `user_id` as a query or path parameter for protected resources.
- **Requirement:** Derive User ID strictly from the `vectorbox_token` via `dependencies.get_current_user`.
- **Ban:** Frontend must NOT send `userId` in API calls (e.g., `getFeed(userId)` is forbidden).

## 2. Recommendation Engine Logic

### Scoring Formula
The final recommendation score is a multiplicative product of **Similarity** and **Quality**.
```python
FinalScore = Similarity (Cosine) * QualityWeight (Sigmoid)
```

### VectorBox Score (Quality Metric)
- **Source:** Aggregated ratings from OMDb (IMDb, Metacritic, Rotten Tomatoes) + Letterboxd.
- **Scale:** 0-100.

### Sigmoid Quality Curve
- **Purpose:** Non-linear boosting of high-quality movies.
- **Formula:** `1 / (1 + e^(-k * (x - x0)))`
- **Parameters:**
    - `x0` (Midpoint): **65**
    - `k` (Steepness): **0.15**

### Feed Generation
- **Diversity:** Implement MMR (Maximal Marginal Relevance) or "Collection Collapsing" to prevent domination by a single franchise.


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

## 3. Frontend Security & Build System

### Package Management
- **Tool:** `pnpm` is the required package manager.
- **Lockfile:** `pnpm-lock.yaml` is the source of truth.

### Security Rules (.npmrc)
- **Location:** `frontend/.npmrc`.
- **Minimum Release Age:** `minimum-release-age=1440` (24 hours) to prevent supply chain attacks via newly published malicious packages.
- **Strict Engine:** `engine-strict=true`.
- **Whitelist:** `minimum-release-age-exclude=browserslist caniuse-lite electron-to-chromium node-releases core-js-compat` (Safe build tools).
- **Overrides:** React 19 peer dependency rules must be enforced via `pnpm.overrides` to accept Next.js 16.1.6 patches.

### Container & Data Persistence
- **Rule:** Ephemeral Containers, Persistent Data.
- **Requirement:** All production data (Postgres/Qdrant volumes) and critical artifacts (Backups) **MUST** be mapped to Host Volumes.
- **Ban:** Never save critical state solely inside the container's writable layer. It will be lost on `docker-compose down`.

## 7. Security Standards (OWASP Hardening)

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

### Pillar 4: Information Leakage
- **Headers:** `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`.
- **Errors:** Stack traces must **NEVER** be returned in `ENVIRONMENT=production`. Return generic "Internal Server Error".

### Visual language (Acid Design)
- **Primary Color:** Neon Green (`#CCFF00`) / `hsl(72, 100%, 50%)`
- **Background:** Deep Black (`#000000`)
- **Typography:** `Space Mono` for headers/data, `Inter` for body.
- **Styling Architecture:** **Tailwind v4 CSS-First**.
    - Configuration handled via `@theme` in `frontend/app/globals.css`.
    - No `tailwind.config.ts` or `tailwind.config.js`.
    - Animations defined as pure CSS keyframes in `globals.css`.
- **Internationalization:** All user-facing strings must be localized via `next-intl` (en/es).

## 4. AI & NLP Layer

### Model Configuration (Dual-Model Strategy)
- **Tier 1 (Speed):** `meta-llama/llama-4-scout-17b-16e-instruct`
    - Use for: Real-time search bar intent parsing.
- **Tier 2 (Intelligence):** `llama-3.3-70b-versatile`
    - Use for: Deep Analysis (Re-ranking), detailed reasoning.

### Cascading Fallback
1.  Tier 1 Primary: Groq `llama-4-scout-17b-16e-instruct`
2.  Tier 2 Primary: Groq `llama-3.3-70b-versatile`
3.  Universal Fallback: OpenAI `gpt-oss-120b` (or `gpt-4o-mini`).

### Structured Output
- **Library:** `instructor` (Python) with Pydantic models.
- **Schema:** `MovieSearchIntent` must be used to filter database queries (years, genres, popularity vibe).
- **Logic:** "Broad Search" - The LLM must expand user keywords into synonyms (e.g., "gangsters" -> "mafia, crime drama, noir") before vector embedding.

## 5. Data Ingestion strategy

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

## 6. Validation & Quality Assurance (v1.2)

### Automated QA
- **Tool:** Playwright (Python).
- **Script:** `tests/qa_automation.py`.
- **Requirements:**
    - **Critical Paths:** Auth, Registration, and Feed Rendering must be covered.
    - **Mobile:** All features must be verifiable on iPhone SE viewport (375px).
    - **Error States:** Custom Acid Design 404/500 pages must be verified.

### Security Audits
- **Backend:** `pip-audit` required before major releases.
- **Frontend:** `pnpm audit` required.
- **Container:** `docker scout` checks recommended.

---

**Last Updated:** 2026-02-18
**Maintained By:** VectorBox Team
