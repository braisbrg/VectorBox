# 🔧 DEVOPS DIRECTOR: Docker, Security & Scripts

> **Role:** DevOps & Infrastructure Lead
> **Domain:** Containerization, Security, Maintenance Scripts
> **Last Updated:** 2026-02-18

This file contains all DevOps rules, security protocols, Docker configuration, and the complete scripts inventory for the VectorBox project.

---

## 1. Container Architecture

### Docker Compose Orchestration
```
┌─────────────────────────────────────────────────────────────────┐
│                     Docker Compose Network                       │
├─────────────────┬─────────────────┬─────────────────────────────┤
│                 │                 │                             │
│  ┌───────────┐  │  ┌───────────┐  │  ┌───────────┐              │
│  │ Frontend  │  │  │  Backend  │  │  │  Postgres │              │
│  │ (Next.js) │──┼──│ (FastAPI) │──┼──│   (15)    │              │
│  └───────────┘  │  └───────────┘  │  └───────────┘              │
│                 │        │        │                             │
│                 │        │        │  ┌───────────┐              │
│                 │        ├────────┼──│  Qdrant   │              │
│                 │        │        │  └───────────┘              │
│                 │        │        │                             │
│                 │        │        │  ┌───────────┐              │
│                 │        └────────┼──│   Redis   │              │
│                 │                 │  │   (7)     │              │
│                 │                 │  └───────────┘              │
└─────────────────┴─────────────────┴─────────────────────────────┘
```

### Services
| Service | Image | Purpose |
| :--- | :--- | :--- |
| `frontend` | Custom (Node 20 Alpine) | Next.js App Router |
| `backend` | Custom (Python 3.11 Slim) | FastAPI API |
| `postgres` | `postgres:15-alpine` | Relational data |
| `qdrant` | `qdrant/qdrant:latest` | Vector store |
| `redis` | `redis:7-alpine` | Cache layer |

---

## 2. Build Optimization

### Frontend: Multi-Stage Build
```dockerfile
# Stage 1: Install dependencies
FROM node:20-alpine AS deps
# ...

# Stage 2: Build application
FROM node:20-alpine AS builder
# ...

# Stage 3: Production image (~150MB)
FROM node:20-alpine AS runner
# Uses output: 'standalone' from next.config.js
```

### Backend: CPU-Optimized PyTorch
- **Base:** `python:3.11-slim`
- **PyTorch:** CPU-only version (no CUDA)
- **Benefit:** Reduced memory footprint, smaller image

---

## 3. Container Hardening

### Non-Root User
```dockerfile
# Backend runs as non-root
RUN useradd -m -u 1000 vectorbox
USER vectorbox
```

### Network Isolation policies
> [!IMPORTANT]
> Database ports usage differs between Development and Production.

- **Development:** Ports 5432 (Postgres) and 6333 (Qdrant) are **exposed** to the host for convenience (debugging tools).
- **Production:** These ports **MUST** be commented out or protected by firewall. Only `backend` container should reach them via internal Docker network.

---

## 4. Supply Chain Security

### Python Dependencies
| Tool | Command | Purpose |
| :--- | :--- | :--- |
| **pip-audit** | `pip-audit --strict` | Scan for CVEs |
| **audit_backend.ps1** | `.\audit_backend.ps1` | Wrapper script |

### JavaScript Dependencies
| Tool | Command | Purpose |
| :--- | :--- | :--- |
| **pnpm audit** | `pnpm audit` | Scan for CVEs |
| **minimum-release-age** | `.npmrc` config | Block new packages |

### Package Publishing Protection
Location: `frontend/.npmrc`

```ini
# .npmrc
minimum-release-age=1440  # 24 hours
engine-strict=true

# Safe exceptions
minimum-release-age-exclude=browserslist caniuse-lite electron-to-chromium node-releases core-js-compat
```

---

## 5. Privacy Protocols

### Email Dropping
> [!CAUTION]
> User email addresses must NEVER be stored or logged.

**Implementation:** The `CSVParser` creates movie dictionaries containing **only**:
- Title
- Year
- Letterboxd URI
- Rating
- Date

The `Email` column from Letterboxd exports is **ignored by design**.

---

## 6. Scripts Inventory

### 🐍 Backend Maintenance Scripts

Located in `backend/scripts/`. Run via Docker execution.

| Script | Description | Command |
| :--- | :--- | :--- |
| **`seed_db.py`** | **The Main Engine.** Fetches movies from TMDB with Spanish metadata (`title_es`, `overview_es`), keywords, and OMDb ratings. Upserts to Postgres + Qdrant. | `docker-compose exec backend python scripts/seed_db.py --limit 100` |
| **`enrich_vectors.py`** | **Data Fixer.** Fetches missing keywords, directors, cast from TMDB. Regenerates embeddings. Use `--all` to force update. | `docker-compose exec backend python scripts/enrich_vectors.py` |
| **`popular_scraper.py`** | **Trends Scraper.** Uses `curl_cffi` (Chrome 120 impersonation) + Regex to fetch "Popular This Week" from Letterboxd AJAX endpoint. Resolves to TMDB IDs. | `docker-compose exec backend python scripts/popular_scraper.py` |
| **`reset_profiles.py`** | **The Refresh Button.** Truncates `user_clusters` table and wipes Redis cache. Forces complete rebuild of user clusters. | `docker-compose exec backend python scripts/reset_profiles.py` |
| **`create_qdrant_indexes.py`** | **Performance.** Creates payload indexes for `vote_count`, `vectorbox_score`, `popularity`, `year`, and `genres`. | `docker-compose exec backend python scripts/create_qdrant_indexes.py` |
| **`test_magic_box.py`** | **NLP Verification.** Stress tests Groq/Llama pipeline to verify query parsing and Qdrant filter construction. | `docker-compose exec backend python scripts/test_magic_box.py` |
| **`security_audit.py`** | **PyTorch Security.** Runs `pip-audit` and checks for dependency vulnerabilities. | `docker-compose exec backend python scripts/security_audit.py` |
| **`wait_for_db.py`** | **Infrastructure.** Blocks boot until Postgres is ready. Used automatically in Docker entrypoint. | *(Internal use only)* |

### 📦 Frontend Utility Scripts

Defined in `frontend/package.json`. Run from host machine in `frontend/` directory.

| Category | Command | Description |
| :--- | :--- | :--- |
| **Security** | `npm run audit:backend` | Triggers `pip-audit` scan inside backend container |
| **Security** | `npm run audit:container` | Runs `docker scout quickview` for image vulnerabilities |
| **Security** | `npm run security-check` | Runs `npm audit` with high severity level |
| **Dev** | `pnpm dev` | Starts Next.js dev server |
| **Linting** | `pnpm lint` | Runs ESLint analysis |

### 🔒 Auditing Protocols

#### Python Vulnerabilities
```bash
docker-compose exec backend pip-audit
```
*Checks all `requirements.txt` packages against the PyPA Advisory Database.*

#### Container Vulnerabilities
```bash
docker scout quickview vectorbox-backend
```
*Checks base images (Postgres, Python Alpine) for system-level CVEs.*

---

## 7. Pre-Commit Hooks

### Husky & Lint-Staged
- **Purpose:** Enforce code quality before commits
- **Checks:**
  - ESLint (frontend)
  - Prettier formatting
  - Type checking

---

## 8. Data Ingestion Strategy

### Hybrid Sync Pipeline
| Method | Purpose | Trigger |
| :--- | :--- | :--- |
| **CSV Import** | Bulk historical data | Manual upload |
| **RSS Feeds** | Real-time "Watched" activity | Scheduled sync |
| **Active Scraping** | User Watchlist (Page 1) | Initial setup |
| **Trending Cache** | Popular on Letterboxd | `popular_scraper.py` |

### Privacy Rule (CSV Import)
> [!WARNING]
> The `email` column must be dropped from memory **immediately** after parsing, before any DB insertion.

---

*For architectural enforcement rules, see [architect.md](../c-suite/architect.md).*
*For backend implementation, see [backend.md](backend.md).*
