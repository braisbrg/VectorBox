# VectorBox

![Version](https://img.shields.io/badge/version-v1.8.1-acidgreen?style=flat-square)
![Last Updated](https://img.shields.io/badge/last_updated-2026--04--18-orange?style=flat-square)

A recommender that actually watches what you watch.

## What is this?

VectorBox reads your Letterboxd history and turns it into a feed that behaves like a smart friend who's seen everything — not a streaming platform's "top picks" trying to sell you the new thing. You point it at your export ZIP or your RSS feed, and it builds a personal taste model across thousands of films, reranks them against global quality signals, and serves them back as themed rows that refresh over time.

It's built for people who care about *what* they watch next, not just that something is on.

## How it works

The recommendation brain is a three-signal system called the **Trident Engine**. Each signal has a different job, and the final feed is a negotiation between them.

- **Signal A — Because You Watched**: picks an anchor film from your history (weighted by rating, recency, and rewatches) and finds vector-space neighbours. This is the "if you liked X, you'll like Y" line of reasoning, but with semantic embeddings instead of collaborative filtering's popularity bias.
- **Signal B — Niche Picks**: rotates through nine curated thematic lenses (Sleep Optional, Slow Burn, Subtitles Required…) that are global, not user-specific. The point isn't to match your clusters — it's to *offer you moods*. Each theme comes with its own genre, era, and language filters. This is the row that changes most often and keeps the feed from feeling stale.
- **Signal C — Hidden Gems**: surfaces high-quality, low-hype films using dynamic thresholds that scale with how much of your history VectorBox has seen. New users get permissive discovery; heavy users get ruthless filtering.

On top of that, two secondary signals lean on your history's *people*: an auteur signal that boosts directors you rate highly, and a cult-actor signal that does the same for repeat cast members.

Everything merges through Reciprocal Rank Fusion, then passes a quality gate (`MOVIE_QUALITY_GATE`) and a sigmoid rescoring pass that discounts films with too few votes to trust.

## Feed Sections

| Section | What it does |
|---|---|
| Because You Watched | Semantic neighbours of a scored anchor from your history |
| Niche Picks | One of 9 rotating global themes with its own filter rules |
| Hidden Gems | High-quality discoveries with low popularity |
| Picked For You | Trident RRF fusion of all three signals |
| Popular on Letterboxd | Scraped Letterboxd trending list |
| Available Now | Unwatched watchlist items streaming on your providers |
| Auteur / Cult Actor | Director- and actor-driven boosts from your ratings |
| Wildcard / Random | Guided chaos and pure chaos, respectively |

Magic Box (natural language search) sits on top of all of this as a 4-tier cascading NLP pipeline — Groq structured parse → OpenAI fallback → keyword heuristic → raw embedding.

## Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 16 (App Router), React 19, Tailwind v4, TanStack Query, Framer Motion |
| Backend | FastAPI, SQLAlchemy 2.0 async, Pydantic v2, APScheduler |
| Data | PostgreSQL 15, Qdrant (vectors), Redis 7 (cache) |
| AI/ML | sentence-transformers, scikit-learn, Groq (Llama), OpenAI (fallback), Instructor |
| Observability | OpenTelemetry + Jaeger |
| Infra | Docker Compose |

## Getting Started

### Prerequisites

- Docker Desktop with Compose v2
- TMDB + OMDb API keys
- At least one of: Groq or OpenAI API key

### Setup

```bash
git clone <repo-url>
cd LetterboxRecommender
cp .env.example .env
# edit .env — fill TMDB_API_KEY, TMDB_READ_TOKEN, OMDB_API_KEY, JWT_SECRET, GROQ_API_KEY
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
| API | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |
| Jaeger | http://localhost:16686 |
| Qdrant | http://localhost:6333/dashboard |

## First run

Create a user, drop in your Letterboxd export ZIP (Settings → Import & Export → Export Data), and wait for the ingestion pipeline to finish. The feed needs about 30 rated films before Signal B and C become useful; under that, Niche Picks and Hidden Gems pull from more permissive pools so the feed isn't empty.

## Documentation

- [PROJECT_MASTER_GUIDE.md](./PROJECT_MASTER_GUIDE.md) — architecture, decisions, full feature catalogue
- [AGENTS.md](./AGENTS.md) — hard architectural rules and anti-patterns
- [STACK_RULES.md](./STACK_RULES.md) — forbidden patterns by layer
- [QA_TESTING_MANUAL.md](./QA_TESTING_MANUAL.md) — manual test protocol

## Version

**v1.8.1** — 2026-04-18

Proprietary & Confidential. All rights reserved.
Contact: vectorbox.app@proton.me
