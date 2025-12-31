# PROJECT MASTER GUIDE: VectorBox

> **Role:** Lead Software Architect Handover
> **Target Audience:** CTO / Senior Developers
> **Version:** 1.0.0
> **Last Updated:** 2025-12-26

This document serves as the absolute source of truth for the **VectorBox** (internal codename: *LetterboxRecommender* / *CineMatch*) project. It documents the existing state of the codebase, detailing architecture, algorithms, and logical flows.

---

## 1. The Technology Stack (The "Engine")

We use a modern, high-performance stack optimizing for **async concurrency** (Backend) and **visual fluidity** (Frontend).

### Frontend
- **Framework:** **Next.js 16.0.7** (App Router). Used for server-side rendering and static optimization.
- **Library:** **React 19.2.1**. Leveraging the latest concurrent features (Suspense, Transitions).
- **Styling:**
  - **Tailwind CSS 3.4**: Utility-first styling for rapid development.
  - **Tailwind Animate**: For simple keyframe animations.
- **Animation:** **Framer Motion 11.11**. Powers all complex transitions, hover states, and carousel physics.
- **Package Manager:** **pnpm**. Enforced via `STACK_RULES.md` for disk space efficiency and strict dependency handling.
- **Design System:** **Acid Design**. High-contrast neon aesthetics (`text-[#CCFF00]`, `bg-black/95`) with `Space Mono` typography.
- **Mobile First:** Fully responsive grid and touch-optimized navigation overlay.

### Backend
- **Framework:** **FastAPI 0.122.0**. Chosen for native async support and high throughput.
- **Server:** **Uvicorn** (Standard Worker).
- **ORM:** **SQLAlchemy 2.0 (Async)**. Uses `asyncpg` driver for non-blocking PostgreSQL access.
- **Validation:** **Pydantic V2**. Strongly typed data models for all inputs/outputs.
- **AI/ML Layer:**
  - **Groq:** Primary Provider.
    - **Tier 1 (Speed):** `llama-4-scout-17b-16e-instruct` (Search Bar).
    - **Tier 2 (Intelligence):** `llama-3.3-70b-versatile` ("Deep Analysis").
  - **Instructor:** Python library to force structured JSON outputs from LLMs.
  - **Instructor:** Python library to force structured JSON outputs from LLMs.
  - **Sentence-Transformers:** Local inference using `all-MiniLM-L6-v2` (**CPU Optimized**) for embedding generation.

### Data Layer
- **Relational DB:** **PostgreSQL 15-alpine**. Stores User profiles, Ratings, Watchlists, Movie metadata, and Clusters.
- **Vector DB:** **Qdrant** (Latest Docker Image). Stores 384-dimensional dense vectors for semantic search.
- **Cache:** **Redis 7-alpine**. Used for:
  - Caching computed feed sections (`feed_service`).
  - Caching TMDB/Provider API responses (`provider_service`, `tmdb_client`).
  - Session/Rate limiting.

### Infrastructure
- **Containerization:** **Docker Compose**. Orchestrates `frontend`, `backend`, `postgres`, `qdrant`, and `redis`.
- **Optimization:** **Multi-Stage Builds** (Frontend) for minimal image size and **PyTorch CPU-only** (Backend) for reduced memory footprint.
- **Security Tools:**
  - **Husky & Lint-Staged:** Pre-commit hooks for code quality.
  - **pip-audit:** Scans Python dependencies for CVEs during build.
  - **npm audit:** Scans JS dependencies.

---

## 2. Feature Catalogue (The "What")

### A. The Feed (Home Page)
The feed is composed of multiple "Sections", generated largely in parallel by `FeedService`.

| Section | Logic / Source |
| :--- | :--- |
| **Available Now** | **Priority Row.** Fetches user's *unwatched* Watchlist items that are currently streaming on their active providers (Netflix, etc.). |
| **Popular on Letterboxd** | Fetches trending movies from TMDB/Letterboxd (cached via `ThreadingService`). |
| **Because you watched [X]** | **Item-Item Collaborative Filtering.** Picks a highly-rated movie from user history, generates a *content-only* vector (ignoring title), and finds similar vectors in Qdrant. |
| **Your Taste ([Cluster])** | **Centroid Search.** Picks one of the user's "Taste Clusters" (e.g., "80s Sci-Fi"), computes the centroid of that cluster, and searches Qdrant. |
| **Hidden Gems** | **Filtered Discovery.** Searches Qdrant near the user's global profile centroid but strictly filters for: `Rating > 7.0`, `Vote Count 50-25k` (filters out blockbusters and trash). |
| **Deep Dive** | **Pure Item-Based.** Uses the weighted "Super Seed" logic (see Algorithms) to find movies similar to the user's favorites. |
| **Comfort Zone (Wildcard)** | **Anti-Recommendation.** Finds highly-rated movies whose genres *do not overlap* with the user's dominant cluster genres. |
| **Random Picks** | Random selection from top 500 "VectorBox Scored" movies in DB. |

### B. The "Magic Box" (NLP Search)
A natural language search interface powered by `nlp_search.py` with Dual-Model Architecture.
1.  **Input:** User types "gangster movies from the 90s that are dark".
2.  **Interpretation (LLM):** Groq (Llama 4 Scout) via `Instructor` parses this into a `MovieSearchIntent` object:
    -   `semantic_query`: "organized crime, mafia, noir, crime drama, 1990s" (Expanded synonyms).
    -   `year_min`: 1990, `year_max`: 1999.
    -   `include_genres`: ["Crime", "Drama"].
    -   `popularity_vibe`: "any".
3.  **Execution:** `QdrantService` executes a vector search with the expanded query, filtering strictly by year and genre.

### C. Tools
- **Group Sync:** (`rss_service.get_group_recommendations_hybrid`) calculates a "Group Vibe" by taking the vectors of multiple users (some from DB, some guests via RSS), finding a centroid, and scoring movies based on *Max Similarity* (rewarding passion) while penalizing movies that *any* single user hates (Similarity < 0.65).
- **Random Picker:** Selects a random movie from a filtered list of recommendations.

### D. Sync System
- **RSS Sync:** `RSSService` pulls `letterboxd.com/username/rss`.
- **Phantom Check:** Detects if a movie on a given `watched_date` in DB matches the RSS feed. If not (and it's a "phantom" duplicate often caused by bad TMDB matching), it deletes it.
- **Match Strategy:** 1. TMDB ID (if present in RSS) -> 2. Letterboxd URI -> 3. Title + Year match.

---

## 3. The "Brain" of VectorBox (The Algorithms)

### The "Trident" Hybrid System (Implemented)
VectorBox generates recommendations using three distinct engines fused via **Reciprocal Rank Fusion (RRF)**:
1.  **Signal A: Vector (Vibe):** Qdrant search using dense embeddings. Captures "Vibe" and plot similarity.
2.  **Signal B: Auteur (Directors):** Boosts movies by directors the user loves (explicit check against user's high-rated history).
3.  **Signal C: Crowd (TMDB):** collaborative filtering from TMDB's vast user data ("People who liked X also liked Y") to ground recommendations in general popularity.

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
2.  **TMDB API:** `TMDBClient` fetches metadata, credits, keywords, and release dates.
3.  **Vector Generation:** `EmbeddingService` creates a synthetic text chunk: `Title + Overview + Genres + Keywords`. This chunk is embedded locally via `SentenceTransformer`.
4.  **Storage:**
    -   **Postgres:** Stores metadata in `movies` table.
    -   **Qdrant:** Stores vector with payload (TMDB ID, Genres, Year).

### Schema Key Points
- **`movies` table:** Stores `vectorbox_score`, extensive ratings (IMDb, RT, Metacritic), and localized `overview_es`.
- **`user_ratings` table:** Links Users to Movies. Contains `is_watched`, `is_watchlist`, `rating`, `watched_date`.
- **`user_clusters` table:** Stores the computed K-Means centroids and labels for each user.

### Caching Strategy
- **Redis (FastAPI Cache):** Caches expensive recommendation endpoints (`get_cluster_recommendations`) for 1 hour.
- **ProviderService:** Caches availability (Netflix/Prime) to avoid hitting TMDB API limit.
- **Invalidation:** `reset_profiles.py` and `ClusteringService` have logic to wipe cache keys when user data changes significantly.

---

## 5. Security & Safety Protocols

### Supply Chain Security
- **`minimum-release-age`:** Configured in `.npmrc` to block packages published < 24 hours ago.
- **Audits:**
  - `audit_backend.ps1` runs `pip-audit --strict`.
  - Frontend `package.json` runs `npm audit`.

### Privacy
- **Email Dropping:** The `CSVParser` creates movie dictionaries containing *only* `{Title, Year, URI, Rating, Date}`. It ignores/drops the `Email` column from Letterboxd exports by virtue of not including it in the output schema.

### Container Hardening
- **User:** Backend container runs as non-root user `cinematch` (UID 1000).
- **Network:** Database ports (5432, 6333) are NOT exposed to the host machine in production configuration (commented out in `docker-compose.yml`), ensuring access only via the internal Docker network.

---

## 6. Directory Structure

### Backend (`/backend`)
- **`routers/`**: API Endpoints (`recommendations.py`, `search.py`, `auth.py`).
- **`services/`**: Business Logic.
  - `feed_service.py`: Orchestrates the Home Feed, responsible for parallel fetching of sections.
  - `clustering_service.py`: Core logic for K-Means, MMR, and recommendation rankings.
  - `nlp_search.py`: "Magic Box" LLM logic using Groq+Instructor.
  - `rss_service.py`: Sync logic for Letterboxd RSS feeds.
- **`models/`**: SQLAlchemy (`database.py`) and Pydantic (`schemas.py`) definitions.
- **`scripts/`**: Maintenance tasks (`seed_db.py`, `enrich_vectors.py`, `popular_scraper.py`, `reset_profiles.py`, `test_magic_box.py`).

### Frontend (`/frontend`)
- **`app/`**: Next.js App Router pages (`page.tsx` for feed).
- **`components/`**: React components.
  - `magic-search.tsx`: The UI for the NLP search bar.
  - `feed-container.tsx`: The main scrollable feed.
  - `recommendation-grid.tsx`: Dispalys movie cards.
- **`ui/`**: Reusable primitives settings (buttons, dialogs).

---
**End of Project Master Guide**
