# VectorBox ![version](https://img.shields.io/badge/version-v1.3-blue) ![Next.js](https://img.shields.io/badge/Next.js-16.1.6-black?logo=next.js) ![FastAPI](https://img.shields.io/badge/FastAPI-0.122.0-009688?logo=fastapi) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791?logo=postgresql) ![Qdrant](https://img.shields.io/badge/Qdrant-vector--db-red) ![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis) ![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker) ![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python) ![TypeScript](https://img.shields.io/badge/TypeScript-5.9.3-3178C6?logo=typescript)

> Letterboxd-connected film recommendation engine powered by vector similarity, director-lineage analysis, and crowd signal fusion.

---

## 📖 Overview

VectorBox ingests a user's Letterboxd history (CSV export, RSS feed, or scraper), builds a personal taste profile, and surfaces recommendations through three parallel signals merged via Reciprocal Rank Fusion (RRF).

**The Trident Engine:**

| Signal | Name | Mechanism |
|--------|------|-----------|
| A | Vibe / Vector | `sentence-transformers` embeddings on plot, genre, and mood |
| B | Auteur / Directors | In-memory director frequency weighting (`Counter` over `movie.directors`) |
| C | Crowd / TMDB | Popularity, vote average, and trending data from TMDB API |

Scores from all three signals are fused with RRF, then re-ranked through a Sigmoid quality function that discounts low-vote-count outliers.

---

## 🗂️ Tech Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| Frontend | Next.js | 16.1.6 | App router, SSR, i18n |
| Frontend | React | 19.2.4 | UI components |
| Frontend | Tailwind CSS | 4.x | Styling |
| Frontend | TypeScript | 5.9.3 | Type safety |
| Frontend | Framer Motion | 12.34.0 | Animations |
| Frontend | TanStack Query | 5.90.21 | Server-state management |
| Backend | FastAPI | 0.122.0 | Async REST API |
| Backend | SQLAlchemy | 2.0.44 | ORM / async DB access |
| Backend | Pydantic | 2.12.4 | Request/response validation |
| Backend | Alembic | 1.13.3 | Schema migrations |
| Backend | APScheduler | 3.10.4 | Background task scheduling |
| Database | PostgreSQL | 15-alpine | Relational data store |
| Database | Qdrant | latest | Vector similarity search |
| Cache | Redis | 7-alpine | Response cache, session store |
| AI/ML | sentence-transformers | 3.0.1 | Embedding generation |
| AI/ML | scikit-learn | 1.7.2 | K-Means clustering |
| AI/ML | Groq SDK | 0.36.0 | LLM NLP (primary) |
| AI/ML | OpenAI SDK | 2.8.1 | LLM NLP (fallback) |
| AI/ML | Instructor | 1.13.0 | Structured LLM output |
| Observability | Jaeger (all-in-one) | latest | Distributed tracing (OTLP) |
| Observability | OpenTelemetry | 1.29.0 | Instrumentation SDK |
| Infrastructure | Docker Compose | — | Container orchestration |
| Security | pip-audit | 2.9.0 | Dependency vulnerability scan |
| Security | slowapi | 0.1.9 | Rate limiting |

---

## ⚡ Quick Start

### Prerequisites

- Docker Desktop (with Compose v2)
- API keys for: TMDB, OMDb, and at least one of Groq or OpenAI

### Step 1 — Clone & Configure

```bash
git clone <repo-url>
cd LetterboxRecommender
cp .env.example .env
```

Edit `.env` and populate every required variable:

| Variable | Required | Description |
|----------|----------|-------------|
| `TMDB_API_KEY` | ✅ | TMDB v3 API key |
| `TMDB_READ_TOKEN` | ✅ | TMDB v4 Bearer read token |
| `OMDB_API_KEY` | ✅ | OMDb API key |
| `DATABASE_URL` | ✅ | Postgres connection string (pre-filled for local) |
| `REDIS_URL` | ✅ | Redis connection string (pre-filled for local) |
| `QDRANT_URL` | ✅ | Qdrant HTTP URL (pre-filled for local) |
| `JWT_SECRET` | ✅ | Random secret for token signing |
| `GROQ_API_KEY` | ⚠️ | Groq LLM key (primary NLP provider) |
| `OPENAI_API_KEY` | ⚠️ | OpenAI key (NLP fallback) |
| `DEFAULT_COUNTRY` | optional | ISO 3166-1 alpha-2 (e.g. `ES`) for streaming providers |
| `ENVIRONMENT` | optional | `development` or `production` (set by setup script) |
| `ALLOWED_ORIGINS` | optional | Comma-separated CORS origins |
| `TRUSTED_HOSTS` | optional | Comma-separated trusted hostnames |

> ⚠️ At least one of `GROQ_API_KEY` or `OPENAI_API_KEY` must be set for the NLP search layer to function.

### Step 2 — Launch

The setup script handles container startup, DB migrations, seeding, index creation, and trend fetching in the correct order.

**Windows (PowerShell):**
```powershell
./setup.ps1
```

**Linux / macOS:**
```bash
chmod +x setup.sh && ./setup.sh
```

**Clean install** (wipes volumes, prunes Docker cache):
```powershell
# Windows
./setup.ps1 -clean

# Linux/macOS
./setup.sh --clean
```

### Step 3 — Access

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| Jaeger Tracing UI | http://localhost:16686 |
| Qdrant Dashboard | http://localhost:6333/dashboard |

---

## 🏗️ Architecture

```
┌──────────────┐        ┌──────────────────────┐
│  Next.js     │──────▶│  FastAPI Backend      │
│  (port 3000) │        │  (port 8000)         │
└──────────────┘        │                      │
                        │  ┌────────────────┐  │
                        │  │ PostgreSQL :5432│  │
                        │  └────────────────┘  │
                        │  ┌────────────────┐  │
                        │  │ Qdrant  :6333  │  │
                        │  └────────────────┘  │
                        │  ┌────────────────┐  │
                        │  │ Redis (internal)│  │
                        │  └────────────────┘  │
                        │  ┌────────────────┐  │
                        │  │ Jaeger  :16686 │  │
                        │  └────────────────┘  │
                        └──────────────────────┘

All services run on a shared Docker Compose bridge network.
Redis is internal-only (no host port).
```

---

## ✨ Features

### Feed Engine

The home feed is composed of eight parallel sections, each powered by a distinct retrieval strategy:

| Section | Strategy |
|---------|----------|
- **Available Now**: Filter by user's streaming services + RRF ranked
- **Cache Guard**: Smart Redis caching that rejects feeds with < 3 sections.
- **Internationalization**: Full i18n support for **English** and **Spanish** via `next-intl` message files in `frontend/messages/`.

### 🔍 Magic Box NLP Search

Free-text search with a 4-tier cascading fallback:

1. **Structured LLM parse** — Groq extracts intent, filters, and mood
2. **OpenAI fallback** — Same structured parse via OpenAI if Groq fails
3. **Keyword fallback** — Regex-based title/genre/year extraction
4. **Raw query fallback** — Direct embedding search on raw input

Each tier includes a **Deep Analysis** mode that expands the query using film-theory reasoning before embedding.

### 🧩 Clustering

- K-Means clustering of the user's watched films
- Dynamic K selection based on library size
- Recency bias: recent watches weighted higher in centroid computation
- MMR (Maximal Marginal Relevance) reranking to reduce result redundancy

### 📥 Letterboxd Sync

- **CSV Import**: Parse and ingest Letterboxd export ZIP files
- **RSS Feed**: Poll user's Letterboxd RSS for incremental updates
- **Scraper**: `curl_cffi`-backed scraper for profiles without RSS

### 👥 Group Recommendations

Aggregate multiple user profiles (RRF merge of individual centroids) to generate recommendations for a group.

### ⏳ Real-time Progress

Long-running operations (sync, enrichment) report progress via `ProgressModal` backed by background task polling.

### 🌐 Internationalization

Full i18n support for **English** and **Spanish** via `next-intl` message files in `frontend/messages/`.

---

## 📋 Scripts Reference

### Backend Scripts (`backend/scripts/`)

| Script | Command | Description |
|--------|---------|-------------|
| `seed_db.py` | `docker-compose exec backend python scripts/seed_db.py --limit <N>` | Seed TMDB data into Postgres and create Qdrant collection |
| `enrich_vectors.py` | `docker-compose exec backend python scripts/enrich_vectors.py` | Generate and upsert sentence-transformer embeddings |
| `popular_scraper.py` | `docker-compose exec backend python scripts/popular_scraper.py` | Fetch trending Letterboxd titles and cache them |
| `create_qdrant_indexes.py` | `docker-compose exec backend python scripts/create_qdrant_indexes.py` | Create payload indexes for filtered vector search |
| `reset_profiles.py` | `docker-compose exec backend python scripts/reset_profiles.py` | Clear all user profiles and vectors |
| `backup_manager.py` | `docker-compose exec backend python scripts/backup_manager.py` | Backup Postgres + Qdrant (see Disaster Recovery) |
| `security_audit.py` | `docker-compose exec backend python scripts/security_audit.py` | Run pip-audit and dependency vulnerability scan |
| `test_magic_box.py` | `docker-compose exec backend python scripts/test_magic_box.py` | Smoke-test NLP search fallback tiers |
| `test_trident_math.py` | `docker-compose exec backend python scripts/test_trident_math.py` | Validate RRF fusion and Sigmoid scoring math |
| `verify_nlp_fallback.py` | `docker-compose exec backend python scripts/verify_nlp_fallback.py` | Confirm 4-tier NLP fallback chain works end-to-end |
| `verify_feed_parallelism.py` | `docker-compose exec backend python scripts/verify_feed_parallelism.py` | Assert feed sections execute in parallel |
| `verify_scoring.py` | `docker-compose exec backend python scripts/verify_scoring.py` | Check Sigmoid quality score output distribution |
| `migrate_release_dates.py` | `docker-compose exec backend python scripts/migrate_release_dates.py` | Backfill release date field in existing records |
| `wait_for_db.py` | (used internally by setup scripts) | Block until Postgres is ready |
| `test_es_whitelist.py` | `docker-compose exec backend python scripts/test_es_whitelist.py` | Test Spanish film whitelist logic |
| `test_idor_hidden_gems.py` | `docker-compose exec backend python scripts/test_idor_hidden_gems.py` | Verify IDOR isolation on Hidden Gems endpoint |

### Host Utility Scripts

| Script | Command | Description |
|--------|---------|-------------|
| `setup.ps1` | `./setup.ps1` or `./setup.ps1 -clean` | Full stack bootstrap (Windows) |
| `setup.sh` | `./setup.sh` or `./setup.sh --clean` | Full stack bootstrap (Linux/macOS) |
| `backup.ps1` | `./backup.ps1` | Trigger backup pipeline (Windows) |
| `backup.sh` | `./backup.sh` | Trigger backup pipeline (Linux/macOS) |

### QA Automation

| Script | Command | Description |
|--------|---------|-------------|
| `frontend/e2e/` | `cd frontend && npx playwright test` | Playwright E2E test suite |

---

## 🔒 Security

### Supply Chain

- **Minimum release age**: `minimum-release-age=1440` in `.npmrc` equivalent; dependencies not published within 24h are blocked
- **Hashed lockfile**: `pip-compile --generate-hashes` produces `requirements.lock` with SHA-256 per package
- **Frontend audit**: `pnpm audit` enforced in CI and via `package.json` audit scripts

### Application

- **IDOR prevention**: All user-scoped queries derive identity from the JWT token, never from a user-supplied ID parameter
- **Session hardening**: Cookies set with `HttpOnly`, `SameSite=Lax`; access tokens are rotated on each authenticated request
- **Rate limiting**: `slowapi` enforces per-IP limits on all public endpoints

### Container

- Backend runs as a **non-root user** inside the container
- In production, PostgreSQL and Redis ports are **not exposed** to the host (internal Docker network only)
- Jaeger OTLP ports (4317, 4318) are internal only in production deployments

### Audit Commands

```bash
# Backend dependency vulnerability scan
docker-compose exec backend python scripts/security_audit.py

# Frontend dependency audit
cd frontend && pnpm audit
```

---

## 🗄️ Disaster Recovery

### Running a Backup

```powershell
# Windows
./backup.ps1

# Linux/macOS
./backup.sh
```

Both scripts delegate to `backend/scripts/backup_manager.py` running inside the backend container.

### What Gets Backed Up

1. **PostgreSQL** — Full `pg_dump` of the `vectorbox` database (schema + data)
2. **Qdrant** — Snapshot of the `movies` collection via the Qdrant HTTP snapshot API

Both artifacts are zipped together into a single archive: `vectorbox_backup_<YYYYMMDD_HHMMSS>.zip`.

### Storage

| Path (Host) | Path (Container) | Contents |
|-------------|-----------------|---------|
| `./backups/` | `/app/backups/` | Timestamped backup ZIP archives |

### Rotation

The backup manager keeps the **last 5 backups** and automatically deletes older archives after each run.

---

## 🧪 QA & Testing

### Playwright E2E Suite

```bash
cd frontend && npx playwright test
```

Covers: authentication flows, Letterboxd sync, feed rendering, NLP search, group recommendations.

### Backend Chaos / Verification Scripts

```bash
# Verify 4-tier NLP fallback chain
docker-compose exec backend python scripts/verify_nlp_fallback.py

# Assert feed sections are parallelised
docker-compose exec backend python scripts/verify_feed_parallelism.py

# Validate RRF + Sigmoid math
docker-compose exec backend python scripts/test_trident_math.py
```

### Full Manual Protocol

See [QA_TESTING_MANUAL.md](./QA_TESTING_MANUAL.md) for the complete step-by-step manual QA protocol.

---

## 🛠️ Development

### Frontend Dev Server

```bash
cd frontend
pnpm dev
```

Starts Next.js on http://localhost:3000 with hot reload.

### Backend Standalone (without full Compose)

```bash
# Start only infrastructure services
docker-compose up postgres qdrant redis jaeger

# Run backend with hot reload
cd backend
uvicorn main:app --reload
```

### Linting

```bash
cd frontend && pnpm lint
```

### Regenerate Backend Lockfile

Run after any change to `requirements.txt`:

```bash
docker-compose exec backend pip-compile --generate-hashes \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  requirements.txt --output-file requirements.lock
```

---

## 📁 Project Structure

```
VectorBox/
├── backend/
│   ├── main.py                 # FastAPI app entry point
│   ├── routers/                # API route definitions
│   ├── services/               # Business logic (feed, NLP, clustering, sync)
│   ├── models/                 # SQLAlchemy ORM models
│   ├── scripts/                # Utility & maintenance scripts (see Scripts Reference)
│   ├── tests/                  # Backend unit / integration tests
│   ├── requirements.txt        # Direct dependencies
│   ├── requirements.lock       # Hashed lockfile (pip-compile)
│   └── Dockerfile
├── frontend/
│   ├── app/                    # Next.js App Router pages
│   ├── components/
│   │   ├── ui/                 # Radix-based headless components
│   │   └── tweak/              # Recommendation tuning controls
│   ├── messages/               # i18n strings (en.json, es.json)
│   ├── package.json
│   └── Dockerfile
├── tests/
│   └── archive/                # Legacy python test archive
├── frontend/
│   ├── e2e/                    # Playwright E2E suite
├── docker-compose.yml
├── setup.ps1                   # Bootstrap script (Windows)
├── setup.sh                    # Bootstrap script (Linux/macOS)
├── backup.ps1                  # Backup trigger (Windows)
├── backup.sh                   # Backup trigger (Linux/macOS)
├── QA_TESTING_MANUAL.md        # Manual QA protocol
├── PROJECT_MASTER_GUIDE.md     # Architecture & decision log
└── .env.example                # Environment variable template
```

---

## 📜 License

**Version:** v1.3.0 Gold Master  
**Last Updated:** 2026-03-11  
**Contact:** vectorbox.app@proton.me
**License:** Proprietary & Confidential. All rights reserved.
