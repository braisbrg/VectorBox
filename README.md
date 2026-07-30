# VectorBox

> Personalized film recommendation engine powered by semantic embeddings and hybrid signal fusion.

## What It Does

VectorBox ingests your film history — via Letterboxd export, RSS feed, or an onboarding carousel — and builds a personal taste model using vector embeddings, director/actor affinity graphs, and collaborative filtering. The result is a multi-section recommendation feed that surfaces films you'd actually want to watch, not just what's trending.

It's built for people who care about *what* they watch next, not just that something is on.

> **Note:** the frontend is mid-migration to a brutalist "ACID" design on `feature/acid-ui-migration` (functionally complete — file cleanup and a pre-release audit remain). CI/CD has a dedicated sprint planned; the Playwright e2e harness predates the Clerk migration and will be rewritten there. The backend, data pipeline, and recommendation internals described below are current.

## How It Works — The Trident Engine

The "Picked For You" core is a three-signal hybrid. Each signal captures a different dimension of taste, and they're fused through Reciprocal Rank Fusion (RRF) before a final diversity pass. (Other feed rows — Because You Watched, Niche Picks, Hidden Gems — are separate builders described under Feed Sections.)

### Signal A — Vibe (Semantic Similarity)
Ranks the whole catalogue against your taste centroid in vector space. Embeddings are generated from LLM-enriched cinematic descriptions — not just plot summaries, but tone, pacing, and visual style — using Groq-hosted open models (`services/llm_models.py` holds the chains: qwen3.6-27b then gpt-oss-120b/20b for enrichment, gpt-oss-120b then qwen3.6-27b for parsing), then encoded with `google/embeddinggemma-300m` (768 dimensions; gated HuggingFace model — requires `HF_TOKEN`). Descriptions are strictly name-free (no titles, directors, or franchises) so the vector space encodes *theme*, not identity; an anti-vector built from your low-rated and rejected films penalizes candidates that resemble things you disliked.

### Signal B — Auteur (Director & Cast Affinity)
Mines your rating history for directors and actors you consistently rate highly (Bayesian-shrunk so two lucky films don't crown a favorite) and surfaces their filmographies you haven't seen.

### Signal C — Crowd (Trakt-Sourced Gems)
Pulls "users also loved" candidates from Trakt for your top films, gated by vector similarity and genre overlap so crowd noise can't drift off-taste. Down-weighted in fusion (`SIGNAL_C_RRF_WEIGHT = 0.7`) so crowd-only films season the row rather than flood it.

### Fusion & Post-Processing
All signals merge through RRF, then pass through a sigmoid quality weighting on VectorBox Score (0–100), director diversity caps (max 2 per director), and MMR reranking for vector-space diversity.

## Tech Stack

### Backend
- **FastAPI** + Python 3.11 — async throughout
- **PostgreSQL 15** + SQLAlchemy 2.0 (async) — film catalog, ratings, clusters
- **Qdrant** — vector database for semantic similarity search
- **Redis 7** — section-level feed caching with per-TTL freshness controls
- **Groq** (qwen3-32b; gpt-oss-120b / qwen3.6-27b in the batch chain) — cinematic description generation
- **google/embeddinggemma-300m** — sentence embeddings (768 dimensions; requires `HF_TOKEN` for the gated model)
- **Trakt API** — Signal C "similar films" source (replaced TMDB recommendations; requires `TRAKT_CLIENT_ID`)
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
- **RSS sync** — automatic incremental updates from your Letterboxd diary feed (early-exit page scan, daily reconcile)
- **Onboarding carousel** — cold-start onboarding without a Letterboxd account (4-signal scale: not for me / ok / liked / loved)
- **Guest mode** — explore recommendations before creating an account
- **Magic Box** — natural language film search (LLM intent parse → editable chips) plus a literal "find a film" title mode
- **Filtered feed** — the console filters (year / runtime / quality / genres / providers) return the full sectioned feed, order-preserving, not a flat result list
- **More Like This** — find similar films using up to 5 seed movies
- **Group recommendations** — find films for multiple users with merged taste profiles (Letterboxd guests welcome), plus shareable group/pair match cards
- **Why this film** — per-recommendation breakdown (trident weights, anchors, embedding neighbours, cluster)
- **Vector space map** — an explorable 2-D projection of your taste clusters
- **Taste card** — shareable PNG profile card (story + square formats)
- **Upcoming movies** — personalized upcoming releases filtered by your genre preferences
- **Content preferences** — tag-based content filtering (avoid jumpscares, gore, slow pacing, etc.)
- **Auteur & Cast signals** — dedicated feed rows for your favorite directors and recurring actors
- **Web-watches CSV export** — films marked watched in-app export back to Letterboxd-importable CSV

## Feed Sections

| Section | Description |
|---|---|
| Because You Watched | Semantic neighbours of a scored anchor from your history |
| Picked For You | Trident RRF fusion of vibe, auteur, and crowd signals |
| Niche Picks | One of 9 rotating global themes with curated filters |
| Hidden Gems | High-quality discoveries with low popularity |
| From Your Favorite Directors | Director-driven recommendations |
| Cast Picks | Actor-driven recommendations |
| Popular on Letterboxd | Scraped trending list with real ★ ratings, filtered against your history |
| Available Now | Unwatched watchlist items on your streaming providers |
| On Your Radar | Personalized upcoming releases (country-aware release badges) |
| Outside Your Comfort Zone | Films from genres you don't usually watch |
| Random Top Picks | Serendipity row |

When console filters are active, the same sections rebuild **filter-aware**: year / runtime / genre / quality apply as output filters (the live ranking with non-matches removed — never re-ranked), providers resolve to an allowed-film set inside the vector search, and rows without enough matches hide.

## Architecture

The recommendation pipeline uses two ID spaces that must not be confused:
- **`Movie.id`** (internal PK) — used for PostgreSQL joins, `UserRating.movie_id`, watched-set deduplication
- **`Movie.tmdb_id`** (TMDB API ID) — used for Qdrant vector indexing, feed-level `seen_ids` deduplication

Feed orchestration runs 11 section-generation tasks in parallel via `asyncio.gather()`, each with its own isolated database session. An anti-vector is pre-computed once before parallelization and shared across signals that need it.

## Getting Started

### Prerequisites

- Docker Desktop with Compose v2
- [TMDB API key](https://www.themoviedb.org/settings/api) + [OMDb API key](https://www.omdbapi.com/apikey.aspx)
- [Groq API key](https://console.groq.com/) (for cinematic descriptions)
- [HuggingFace token](https://huggingface.co/settings/tokens) with access granted to `google/embeddinggemma-300m` (gated model; required at first launch to download the embedding model)
- [Trakt API client ID](https://trakt.tv/oauth/applications) (free; powers Signal C "similar films")
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

See [SCRIPTS_GUIDE.md](./docs/SCRIPTS_GUIDE.md) for the full catalogue of maintenance, backup, and data scripts.

## Development

- **Branch strategy:** `develop` for active work, `feature/*` for significant changes, `master` for tagged releases only.
- **Commit format:** `feat:`, `fix:`, `refactor:`, `perf:`, `docs:`
- **Package manager:** pnpm (frontend), pip with hash-verified lockfile (backend)
- **Backend commands** run inside Docker: `docker compose exec backend ...`

### Two traps that will cost you an afternoon

**Your edit is probably not running.** Uvicorn auto-reload does not fire on
host-side edits under Windows — bind-mount file events never reach the container
watcher. The frontend container serves a production build, so it does not
hot-reload at all.

```bash
docker compose restart backend            # after ANY backend change
docker compose up -d --build frontend     # after ANY frontend change
```

A change that type-checks is not a change that ran. If feed logic changed, also
flush the `section:*` Redis keys: the cache-save block refreshes TTLs on
cache-hit sections, so stale content self-perpetuates.

**Groq's free tier caps at 8000 tokens per MINUTE**, not per day, and one Magic
Box parse costs ~2000. Four searches in quick succession exhaust it, after which
the parser returns nothing and every answer degrades. Any burst test needs pacing
or it measures the rate limiter instead of the engine.

### Tests

```bash
docker compose exec backend python -m pytest -q                    # hermetic
docker compose exec backend python scripts/verify_search_branches.py --repeat 3
```

The default suite touches no external service; the few that do are marked
`integration` and deselected. `verify_search_branches.py` pins search behaviour
using `forced_intent`, which bypasses the LLM entirely — so it is deterministic,
repeatable, and free. Prefer it to anything that measures *through* the parser.

Three tests are contract guards, each closing a class of bug that fails in
silence — something declared that never gets applied:

| test | what it refuses to let happen |
|---|---|
| `test_qdrant_filter_contract` | a filter key written but never handled, so the search silently runs without it |
| `test_qdrant_payload_writers` | a payload writer that omits a key, which DELETES it from the point |
| `test_no_dead_parameters` | a parameter accepted and never read, so every caller passing it is ignored |

## License

[MIT](./LICENSE)
