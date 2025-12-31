# VectorBox Scripts Guide

A comprehensive inventory of maintenance, security, and utility scripts for the internal DevOps / Architecture team.

## 🐍 Backend Maintenance Scripts
All Python scripts located in `backend/scripts/`. Run these via Docker execution.

| Script | Description | Command (Safe to Run) |
| :--- | :--- | :--- |
| **`seed_db.py`** | **The Main Engine.** Fetches movies from TMDB with **Spanish Metadata** (`title_es`, `overview_es`), **Keywords**, and **OMDb Ratings** (Score adjustment). Upserts to Postgres + Qdrant. | `docker-compose exec backend python scripts/seed_db.py --limit 100` |
| **`enrich_vectors.py`** | **Data Fixer.** Iterates over movies, fetches missing keywords, **Directors, and Cast** from TMDB, and regenerates/upserts embeddings. Use `--all` to force update tokens/genres. | `docker-compose exec backend python scripts/enrich_vectors.py` |
| **`popular_scraper.py`** | **Trends Scraper.** Fetches "Popular This Week" from Letterboxd HTML, resolves Slugs to TMDB IDs, and caches in Redis with **24h TTL**. | `docker-compose exec backend python scripts/popular_scraper.py` |
| **`reset_profiles.py`** | **"The Refresh Button".** Forces a complete rebuild of User Clusters. Truncates `user_clusters` table and wipes Redis cache. | `docker-compose exec backend python scripts/reset_profiles.py` |
| **`test_magic_box.py`** | **NLP Verification.** Runs a stress test on the Groq/Llama 3.3 pipeline to verify query parsing and Qdrant filter construction. | `docker-compose exec backend python scripts/test_magic_box.py` |
| **`wait_for_db.py`** | **Infrastructure.** Blocks boot until Postgres is ready using `socket` check. Used automatically in Docker entrypoint. | *(Internal use only)* |

## 📦 Frontend Utility Scripts
Commands defined in `frontend/package.json`. Run these from the host machine inside the `frontend/` directory.

| Network | Command | Description |
| :--- | :--- | :--- |
| **Security** | `npm run audit:backend` | Triggers a `pip-audit` scan inside the running backend container to check Python dependencies for CVEs. |
| **Security** | `npm run audit:container` | Runs `docker scout quickview` to analyze image vulnerabilities. |
| **Security** | `npm run security-check` | Runs `npm audit` with high severity level. |
| **Dev** | `pnpm dev` | Starts Next.js dev server (Host only). |
| **Linting** | `pnpm lint` | Runs ESLint analysis. |

## 🕵️ Security & Audit
Standard auditing protocols for this project.

1.  **Python Vulnerabilities:**
    ```bash
    docker-compose exec backend pip-audit
    ```
    *Checks all installed `requirements.txt` packages against the PyPA Advisory Database.*

2.  **Container Vulnerabilities:**
    ```bash
    docker scout quickview cinematch-backend
    ```
    *Checks base images (Postgres, Python Alpine) for system-level CVEs.*
