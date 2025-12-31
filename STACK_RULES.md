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
- **Minimum Release Age:** `minimum-release-age=1440` (24 hours) to prevent supply chain attacks via newly published malicious packages.
- **Whitelist:** `minimum-release-age-exclude=browserslist caniuse-lite electron-to-chromium node-releases core-js-compat` (Safe build tools).
- **Overrides:** React 19 peer dependency rules must be enforced via `pnpm.overrides` to accept Next.js 16.0.7 patches.

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
3.  **Active Scraping:** For fetching User Watchlist (Page 1) to seed initial preferences.
4.  **Trending Cache:** Redis-cached "Popular on Letterboxd" identifiers.

---

**Last Updated:** 2025-12-05
**Maintained By:** VectorBox Team
