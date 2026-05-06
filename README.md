# VectorBox

> Personalized film recommendation engine powered by semantic embeddings and hybrid signal fusion.

## What It Does

VectorBox ingests your film history — via Letterboxd export, RSS feed, or an onboarding carousel — and builds a personal taste model using vector embeddings, director/actor affinity graphs, and collaborative filtering. The result is a multi-section recommendation feed that surfaces films you'd actually want to watch, not just what's trending.

It's built for people who care about *what* they watch next, not just that something is on.

## ⚠️ Setup & CI/CD not updated

## How It Works — The Trident Engine

The recommendation core is a three-signal hybrid system. Each signal captures a different dimension of taste, and they're fused through Reciprocal Rank Fusion (RRF) before a final diversity pass.

### Signal A — Because You Watched (Semantic Similarity)
Picks a high-quality anchor from your history (scored by rating × recency decay × rewatch boost) and retrieves vector-space neighbours from Qdrant. Embeddings are generated from LLM-enriched cinematic descriptions — not just plot summaries, but tone, pacing, and visual style — using Groq's LLaMA models, then encoded with `all-MiniLM-L6-v2` (384 dimensions). An anti-vector built from your low-rated and rejected films penalizes candidates that resemble things you disliked.

### Signal B — Niche Picks (Thematic Discovery)
Nine curated global themes (*Sleep Optional*, *Slow Burn*, *Subtitles Required*, *Bring Tissues*…) rotate automatically per feed refresh. Each theme carries its own genre, era, and language filters. This isn't taste-matching — it's mood-offering, designed to keep the feed from going stale.

### Signal C — Hidden Gems (Quality Discovery)
Surfaces high-quality, low-popularity films directly from Postgres with dynamic thresholds that scale with your history size. New users get permissive discovery; users with 100+ rated films get stricter filtering. Non-English films receive an exoticism boost. Candidates are re-ranked using 30% vector similarity to your taste profile.

### Fusion & Post-Processing
All signals merge through RRF, then pass through a sigmoid quality weighting on VectorBox Score (0–100), director diversity caps (max 2 per director), and MMR reranking for vector-space diversity.

## Tech Stack

### Backend
- **FastAPI** + Python 3.11 — async throughout
- **PostgreSQL 15** + SQLAlchemy 2.0 (async) — film catalog, ratings, clusters
- **Qdrant** — vector database for semantic similarity search
- **Redis 7** — section-level feed caching with per-TTL freshness controls
- **Groq** (LLaMA 4 Scout) — cinematic description generation
- **all-MiniLM-L6-v2** — sentence embeddings (384 dimensions)
- **Clerk** — authentication (JWKS-based JWT verification)

### Frontend
- **Next.js 16** (App Router) + React 19
- **Tailwind CSS v4**
- **Framer Motion** — animations and transitions
- **TypeScript** — strict typing throughout

### Infrastructure
- **Docker Compose** — local development (Postgres, Qdrant, Redis, backend, frontend)
- **Alembic** — database migrations
- **OpenTelemetry + Jaeger** — distributed tracing

## Key Features

- **Letterboxd ZIP import** — full watch history, ratings, watchlist, diary, and liked films
- **RSS sync** — automatic incremental updates from your Letterboxd diary feed
- **Onboarding carousel** — cold-start onboarding without a Letterboxd account
- **Guest mode** — explore recommendations before creating an account
- **Magic Box** — natural language film search with cascading NLP pipeline (structured parse → keyword heuristic → raw embedding)
- **More Like This** — find similar films using up to 5 seed movies
- **Group recommendations** — find films for multiple users with merged taste profiles
- **Upcoming movies** — personalized upcoming releases filtered by your genre preferences
- **Content preferences** — tag-based content filtering (avoid jumpscares, gore, slow pacing, etc.)
- **Auteur & Cast signals** — dedicated feed rows for your favorite directors and recurring actors

## Feed Sections

| Section | Description |
|---|---|
| Because You Watched | Semantic neighbours of a scored anchor from your history |
| Picked For You | Trident RRF fusion of vibe, auteur, and crowd signals |
| Niche Picks | One of 9 rotating global themes with curated filters |
| Hidden Gems | High-quality discoveries with low popularity |
| From Your Favorite Directors | Director-driven recommendations |
| Cast Picks | Actor-driven recommendations |
| Popular on Letterboxd | Scraped trending list, filtered against your history |
| Available Now | Unwatched watchlist items on your streaming providers |
| Outside Your Comfort Zone | Films from genres you don't usually watch |
| Random Top Picks | Serendipity row |

## Architecture

The recommendation pipeline uses two ID spaces that must not be confused:
- **`Movie.id`** (internal PK) — used for PostgreSQL joins, `UserRating.movie_id`, watched-set deduplication
- **`Movie.tmdb_id`** (TMDB API ID) — used for Qdrant vector indexing, feed-level `seen_ids` deduplication

Feed orchestration runs 10 section-generation tasks in parallel via `asyncio.gather()`, each with its own isolated database session. An anti-vector is pre-computed once before parallelization and shared across signals that need it.

## Getting Started

### Prerequisites

- Docker Desktop with Compose v2
- [TMDB API key](https://www.themoviedb.org/settings/api) + [OMDb API key](https://www.omdbapi.com/apikey.aspx)
- [Groq API key](https://console.groq.com/) (for cinematic descriptions)
- [Clerk account](https://clerk.com/) (for authentication)
- A Letterboxd account (optional — can use the onboarding carousel instead)

### Setup

```bash
git clone https://github.com/braisbrg/vectorbox.git
cd vectorbox
cp .env.example .env
# Fill in your API keys in .env
```

### Launch

**Windows (PowerShell):**
```powershell
./setup.ps1
```

**Linux / macOS:**
```bash
chmod +x setup.sh && ./setup.sh
```

Add `-clean` / `--clean` for a fresh volume wipe.

### Access

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API Docs | http://localhost:8000/docs |
| Jaeger (tracing) | http://localhost:16686 |
| Qdrant Dashboard | http://localhost:6333/dashboard |

### First Run

Create an account, then either import your Letterboxd export ZIP (Settings → Import & Export → Export Data) or use the onboarding carousel to rate 15+ films. The feed needs about 30 rated films before all signals become useful; under that threshold, the engine uses more permissive pools so the feed isn't empty.

## Scripts

See [SCRIPTS_GUIDE.md](./SCRIPTS_GUIDE.md) for the full catalogue of maintenance, backup, and data scripts.

## Development

- **Branch strategy:** `develop` for active work, `feature/*` for significant changes, `main` for tagged releases only.
- **Commit format:** `feat:`, `fix:`, `refactor:`, `perf:`, `docs:`
- **Package manager:** pnpm (frontend), pip with hash-verified lockfile (backend)
- **Backend commands** run inside Docker: `docker-compose exec backend ...`

## License

[MIT](./LICENSE)
