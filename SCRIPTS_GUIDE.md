# VectorBox Scripts Guide

A comprehensive inventory of maintenance, security, and utility scripts for the internal DevOps / Architecture team.

## 🐍 Backend Maintenance Scripts
All Python scripts located in `backend/scripts/`. Run these via Docker execution.

| Script | Description | Command (Safe to Run) |
| :--- | :--- | :--- |
| **`maintenance_orchestrator.py`** | **Master Orchestrator.** Runs the full DB maintenance pipeline in 5 phases respecting OMDb daily budget (`api_budget` table) and Groq daily limits. Phases: (1) refresh_metadata for missing `imdb_vote_count` or stale, (2) embedding_audit for NULL `embedding_quality_score`, (3) embedding_repair via Groq for low-quality / not-yet-enriched, (4) backfill cinematic descriptions, (5) reset user clusters. Stops gracefully on budget exhaustion. Resumable across runs. | `docker-compose exec backend python scripts/maintenance_orchestrator.py [--phases 1,2,3] [--omdb-budget 1000] [--embed-limit 500] [--dry-run]` |
| **`seed_db.py`** | **The Main Engine.** Uses `MovieFactory` to fetch movies from TMDB with **Spanish Metadata**, **Keywords**, and strict Pydantic **OMDb Ratings**. Upserts to Postgres + Qdrant. Supports `--strategy popular\|recent\|upcoming`. | `docker-compose exec backend python scripts/seed_db.py --limit 100 [--strategy popular\|recent\|upcoming]` |
| **`refresh_metadata.py`** | **Metadata Refresher.** Fetches fresh `vote_count`, `vote_average`, `popularity`, `poster_path`, `genres`, `runtime` from TMDB and recalculates `vectorbox_score` for movies already in DB. Selects movies by age cohort. Use `--dry-run` to preview. | `docker-compose exec backend python scripts/refresh_metadata.py --strategy recent --limit 200` |
| **`enrich_vectors.py`** | **Data Fixer & LLM Embeddings.** Fetches missing keywords/credits from TMDB. Uses Groq to generate 80-word cinematic descriptions and upserts 384d semantic vectors. Run with `--enrich-embeddings` to process LLM upgrades. Supports `--model-only [scout\|70b\|8b]` for precise rate-limit throttling and `--reset-enrichment` for a fresh start. | `docker-compose exec backend python scripts/enrich_vectors.py [--enrich-embeddings] [--model-only scout]` |
| **`backfill_descriptions.py`** | **Description Backfiller.** Fills `cinematic_description` for movies that already have LLM embeddings (`has_enriched_embedding=True`) but no saved description. Does NOT regenerate embeddings — only calls the LLM and saves the text. Handles `DailyLimitExhausted` gracefully (commits progress and stops). Use `--dry-run` to count. | `docker-compose exec backend python scripts/backfill_descriptions.py [--limit N] [--dry-run]` |
| **`popular_scraper.py`** | **Trends Scraper.** Fetches "Popular This Week" from Letterboxd HTML, resolves Slugs to TMDB IDs, and caches in Redis with **24h TTL**. | `docker-compose exec backend python scripts/popular_scraper.py` |
| **`reset_profiles.py`** | **"The Refresh Button".** Forces a complete rebuild of User Clusters. Truncates `user_clusters` table and wipes Redis cache. | `docker-compose exec backend python scripts/reset_profiles.py` |
| **`test_magic_box.py`** | **NLP Verification.** Runs a stress test on the 4-Tier Cascading Fallback pipeline to verify query parsing and Qdrant filter construction. | `docker-compose exec backend python scripts/test_magic_box.py` |
| **`verify_nlp_fallback.py`** | **Chaos Monkey.** Mocks failures in 1st/2nd tier LLM clients to guarantee that the application successfully cascades down to the universal fallback tiers without crashing. | `docker-compose run --rm backend python scripts/verify_nlp_fallback.py` |
| **`test_es_whitelist.py`** | **QA Whitelist.** Unit tests the pure standalone function `filter_es_providers` to guarantee disallowed streaming services don't reach the frontend. | `docker-compose run --rm backend python scripts/test_es_whitelist.py` |
| **`security_audit.py`** | **Security Audit.** Runs `pip-audit --require-hashes` against `requirements.lock` for strict hash-verified CVE scanning. Falls back to `pip freeze` + `--no-deps` if no lockfile is present. Ignores known false positives (torchvision CPU builds, diskcache). | `docker-compose exec backend python scripts/security_audit.py` |
| **`verify_feed_parallelism.py`** | **QA Certification (Phase 2).** Mocks 11 feed tasks with 200ms latency, confirms `asyncio.gather` runs concurrently (total < 400ms), and verifies each task uses an isolated session object. | `docker-compose exec backend python scripts/verify_feed_parallelism.py` |
| **`test_idor_hidden_gems.py`** | **QA Certification (Phase 3).** Calls `/api/recommendations/hidden-gems` without auth cookie, verifies 401 response. Also tests forged `user_id` query param is ignored. | `docker-compose exec backend python scripts/test_idor_hidden_gems.py` |
| **`test_trident_math.py`** | **QA Certification (Phase 4).** Verifies sigmoid curve outputs at score=50/65/80 against expected weights, and tests RRF correctness by asserting movies in multiple lists score higher than single-list entries. | `docker-compose exec backend python scripts/test_trident_math.py` |
| **`wait_for_db.py`** | **Infrastructure.** Blocks boot until Postgres is ready using `socket` check. Used automatically in Docker entrypoint. | *(Internal use only)* |
| **`seed_qa_user.py`** | **QA Preparation.** Creates the synthetic `qa_vecbox` user profile with predefined movie ratings and runs forced vector clustering. Used to achieve a deterministic state before running the QA3 verification protocol. | `docker-compose exec backend python scripts/seed_qa_user.py` |
| **`backup_manager.py`** | **Disaster Recovery.** Creates a comprehensive snapshot of Postgres (Schema + Data), Qdrant (Shards), and Redis (dump.rdb), zips them, and rotates old backups (Max 5). | `docker-compose exec backend python scripts/backup_manager.py` |
| **`restore_manager.py`** | **Disaster Recovery.** Takes a ZIP archive generated by `backup_manager`, wipes current collections and DB connections, and restores Postgres, Qdrant, and Redis to the archived state. Use `--dry-run` to preview operations. | `docker-compose exec backend python scripts/restore_manager.py /app/backups/[file].zip` |
| **`reconcile_letterboxd_movies.py`** | **Data Reconciliation.** Audits all movies with `letterboxd_uri`, verifies their `tmdb_id` against TMDB search (year tolerance ≤1), and optionally fixes mismatches with `--fix` by re-ingesting the correct movie and migrating `UserRating` records. | `docker-compose exec backend python scripts/reconcile_letterboxd_movies.py [--fix]` |
| **`fix_movies_manual.py`** | **Manual Corrections.** Reads a CSV (`corrections.csv`) of `letterboxd_uri,correct_tmdb_id,old_tmdb_id` corrections, re-ingests the correct movie via `MovieService`, migrates `UserRating` records, and deletes orphans. Supports `--dry-run` and `--file`. | `docker-compose exec backend python scripts/fix_movies_manual.py [--dry-run] [--file path]` |
| **`reenrich_movies.py`** | **Metadata Recovery.** Targets movies with existing VectorBox scores that are missing critical metadata (IMDb rating, etc) and re-runs the full enrichment pipeline. | `docker-compose exec -e PYTHONPATH=/app backend python -m scripts.reenrich_movies [--limit 100]` |
| **`create_qdrant_indexes.py`** | **Index Setup.** Creates payload indexes on the Qdrant `movies` collection (genres, year, popularity, etc.) to accelerate filtered vector searches. | `docker-compose exec backend python scripts/create_qdrant_indexes.py` |
| **`heal_vectors.py`** | **Vector Recovery.** Scans all movies in Postgres, identifies those missing Qdrant vectors, re-generates embeddings via `EmbeddingService`, and upserts them. | `docker-compose exec backend python scripts/heal_vectors.py` |
| **`migrate_release_dates.py`** | **Schema Migration.** One-time migration that adds the `release_dates JSONB` column to the `movies` table. Safe to re-run (`ADD COLUMN IF NOT EXISTS`). | `docker-compose exec backend python scripts/migrate_release_dates.py` |
| **`verify_qa_pt2.py`** | **QA Certification (Phase 5).** End-to-end HTTP check against a running stack: logs in as `qa_vecbox` and validates the `/api/recommendations/feed` response. | `docker-compose exec backend python scripts/verify_qa_pt2.py` |
| **`debug_movie.py`** | **Diagnostic.** Inspects a single movie: DB metadata, top-10 Qdrant vector neighbors, and (with `--user-id`) signal-by-signal eligibility analysis (Signal A anchor similarity, Auteur director rank, Signal C TMDB rec presence). Lookup by title (fuzzy) or `--tmdb-id`. | `docker-compose exec backend python scripts/debug_movie.py "Spirited Away" --user-id 212` |
| **`recalc_vbs_from_db.py`** | **VBS Backfill (no API).** Recomputes `vectorbox_score` for every movie using existing DB columns (`imdb_rating`, `metacritic_rating`, `vote_average`, `imdb_vote_count`, `vote_count`) — **does NOT hit OMDb**. Run this after any change to the VBS formula in `omdb_client.py` to consolidate the catalogue in seconds. Reports updated/cleared/unchanged counts and average delta. | `docker compose exec backend python scripts/recalc_vbs_from_db.py` |
| **`reembed_catalog.py`** | **Bulk Re-embedding.** Regenerates Qdrant vectors for the entire catalogue using `cinematic_description` (preferred, ~99% coverage) or `overview + genres + keywords` (fallback). NEVER includes title — title-token leakage causes off-theme BYW/Magic Box neighbours. Same model (all-MiniLM-L6-v2, 384-dim) and same Qdrant collection — just upserts new vectors. After running, re-cluster with `reset_profiles.py --force` and flush Redis. ~3-5 min for 7500 films. No API hits. | `docker compose exec backend python scripts/reembed_catalog.py` |
| **`experiment_embeddings.py`** | **Embedding A/B Comparison.** Curated 80-film pool with 7 anchors (Howl's, Deprisa Deprisa, Pan's Labyrinth, Inception, Godfather, Goodfellas, Spirited Away) and known thematic neighbours. Compares 4-5 embedding variants (current Qdrant baseline, MiniLM-no-title, multilingual-MiniLM, multilingual-e5-small, optionally EmbeddingGemma if HF token present) on top-8 hit rate. Output is a tabular qualitative report — use to decide before any model change or major re-embedding. | `docker compose exec backend python scripts/experiment_embeddings.py` |
| **`experiment_signal_c.py`** | **Signal C filter A/B.** Curated 12-film pool (4 niche + 4 art-house + 4 popular). For each seed fetches both TMDB endpoints (`/recommendations` and `/similar`), computes vector cosine + genre overlap vs the seed, and tabulates which candidates would survive each filter strategy (raw, vec≥0.45, vec≥0.50, genre-only, combos). Reports aggregate pass-rate per strategy across all seeds + multi-seed agreement analysis for `user_id=212`. Use before tuning `SIGNAL_C_VEC_SIM_THRESHOLD` or before swapping data sources. | `docker compose exec backend python scripts/experiment_signal_c.py` |
| **`experiment_trakt.py`** | **Alternative Signal C source comparison.** Same 12-seed pool as `experiment_signal_c.py` but pulls related films from **Trakt API** (`/movies/{id}/related`) instead of TMDB. Use to evaluate whether Trakt's user-behaviour-based recs are higher quality than TMDB's noisy collab filter, especially for niche/recent/non-English films. Requires `TRAKT_CLIENT_ID` env var (free, sign up at https://trakt.tv/oauth/applications). Reports catalogue-coverage (% of recs already in our DB) and pass-rate per filter strategy. | `docker compose exec backend python scripts/experiment_trakt.py` |
| **`check_embeddings.py`** | **Embedding Sanity Check.** Compares stored Qdrant vectors against a reference MiniLM embedding built from `title + year + genres + directors`. Flags movies below cosine threshold as likely-corrupt. Flags: `--update-db` persist score, `--fix` re-enrich flagged, `--user-id` scope to one user, `--tmdb-id` check a single movie, `--recheck` re-run on movies that already have a score (default skips them), `--verbose` print reference text + first 5 dims of stored/reference vectors, `--threshold` let's you change the threshold value to flag movies | `docker-compose exec backend python scripts/check_embeddings.py --tmdb-id 129 --verbose --recheck --threshold 0.5` |
| **`fix_qdrant_ids.py`** | **Qdrant ID Audit (T-04).** Walks every Qdrant point and classifies it as modern (`point.id == Movie.tmdb_id`), legacy (`point.id == Movie.id`, requires migration to tmdb_id), or orphan (no DB record). Migrates legacy points by re-upserting under the correct tmdb_id and deleting the legacy point. Dry-run by default; pass `--execute` to apply. `--delete-orphans` (requires `--execute`) wipes points with no DB record. | `docker-compose exec backend python scripts/fix_qdrant_ids.py [--execute] [--delete-orphans] [--limit 200]` |
| **`test_guest_feed.py`** | **Recommendation Quality QA.** Tests the `/public/guest-feed` recommendation logic offline. Accepts a JSON ratings dict, a DB user ID, or a named preset (`cinephile`, `blockbuster`). Reports VectorBox score distribution, genre distribution, top-10 results, and genre coverage (% of positive-seed genres represented in recs). | `docker compose exec backend python scripts/test_guest_feed.py --preset cinephile` |

### seed_db.py — `--strategy` details

| Strategy | Source | Sort | Use case |
| :--- | :--- | :--- | :--- |
| `popular` *(default)* | TMDB Discover | `vote_count.desc` | Bulk seed with well-known films |
| `recent` | TMDB Discover, last 90 days | `primary_release_date.desc` | Keep DB current with new releases |
| `upcoming` | TMDB Discover, next 180 days | `primary_release_date.asc` | Seed upcoming films, sets `is_upcoming=True` + fetches per-country release dates |

```bash
# Default: popular films
docker-compose exec backend python scripts/seed_db.py --limit 500

# Recent releases (last 90 days)
docker-compose exec backend python scripts/seed_db.py --limit 200 --strategy recent

# Upcoming films (next 180 days)
docker-compose exec backend python scripts/seed_db.py --limit 100 --strategy upcoming
```

### maintenance_orchestrator.py (master)

Single entry point for routine DB maintenance. Replaces ad-hoc sequencing of `refresh_metadata`, `check_embeddings`, `enrich_vectors`, `backfill_descriptions`, and `reset_profiles`.

**Phases (run in order; filter with `--phases`):**

| # | Name | API used | Stop condition |
|---|---|---|---|
| 1 | `refresh_metadata` | OMDb + TMDB | OMDb daily budget reached |
| 2 | `embedding_audit` | none (local MiniLM) | `--embed-limit` |
| 3 | `embedding_repair` | Groq | `DailyLimitExhausted` or `--embed-limit` |
| 4 | `backfill_descriptions` | Groq | `DailyLimitExhausted` or `--embed-limit` |
| 5 | `reset_profiles` | none | runs once over all users with `onboarding_completed` |

**Arguments:**
- `--phases 1,2,3,4,5` — comma-separated phases to run (default: all)
- `--omdb-budget N` — max OMDb calls for this run, capped by remaining daily quota in `api_budget` table (default: 1000)
- `--embed-limit N` — max movies per embedding phase 2/3/4 (default: 500)
- `--dry-run` — preview targets without writing

**Recommended cadence:**
```bash
# Daily — keep recent metadata fresh + drain Groq daily quota for repair
0 3 * * *  docker compose exec -T backend python scripts/maintenance_orchestrator.py --phases 1,2,3 --omdb-budget 1000 --embed-limit 200

# Weekly — backfill descriptions and re-cluster
0 4 * * 0  docker compose exec -T backend python scripts/maintenance_orchestrator.py --phases 4,5
```

**One-off catch-up (after big formula changes / mass ingest):**
```bash
# Day 1: drain OMDb quota
docker compose exec backend python scripts/maintenance_orchestrator.py --phases 1 --omdb-budget 1000

# Day 2-N: repeat Phase 1 until imdb_vote_count populated everywhere; then run 2-5.
docker compose exec backend python scripts/maintenance_orchestrator.py --phases 2,3,4,5 --embed-limit 1000
```

### refresh_metadata.py (legacy single-phase)

Refreshes movie metadata and recalculates `vectorbox_score` for existing DB movies.

**Arguments:**
- `--strategy [recent|mid|classic|all]`
  - `recent` — movies < 1 year old, refresh if not updated in 7 days
  - `mid` — movies 1-5 years old, refresh if not updated in 30 days
  - `classic` — movies > 5 years old, refresh if not updated in 90 days
  - `all` — all three strategies combined
- `--limit N` — max movies to process per run (default: 100)
- `--dry-run` — show what would be refreshed without updating

**Recommended cron schedule:**
```
# Daily: refresh recent movies
0 3 * * * docker-compose exec -T backend python scripts/refresh_metadata.py --strategy recent --limit 200

# Weekly: refresh mid-range movies
0 4 * * 0 docker-compose exec -T backend python scripts/refresh_metadata.py --strategy mid --limit 500

# Monthly: refresh classics
0 5 1 * * docker-compose exec -T backend python scripts/refresh_metadata.py --strategy classic --limit 1000
```

## 📦 Frontend Utility Scripts
Commands defined in `frontend/package.json`. Run these from the host machine inside the `frontend/` directory.

| Network | Command | Description |
| :--- | :--- | :--- |
| **Security** | `pnpm run audit:backend` | Triggers a `pip-audit` scan inside the running backend container to check Python dependencies for CVEs. |
| **Security** | `pnpm run audit:container` | Runs `docker scout quickview` to analyze image vulnerabilities. |
| **Security** | `pnpm run security-check` | Runs `pnpm audit` with high severity level. |
| **Dev** | `pnpm dev` | Starts Next.js dev server (Host only). |
| **Linting** | `pnpm lint` | Runs ESLint analysis. |

## 🛠️ Host Utility Scripts
Run these from the root directory of the project on your host machine.

| Script | Description |
| :--- | :--- |
| **`setup.ps1`** | **Windows.** Master setup script. Automatically uses `docker-compose.prod.yml` if `ENVIRONMENT=production` is in `.env`. Use `./setup.ps1 -clean` for a deep system wipe. |
| **`setup.sh`** | **Linux/Mac.** Master setup script. Automatically uses `docker-compose.prod.yml` if `ENVIRONMENT=production` is in `.env`. Use `./setup.sh --clean` for a deep system wipe. |
| **`backup.ps1`** | **Windows.** Wrapper to execute the backup manager. |
| **`backup.sh`** | **Linux/Mac.** Wrapper to execute the backup manager. |
| **`cd frontend && npx playwright test`** | **QA Suite.** Runs Playwright automation to verify core flows (Auth, Mobile, etc). |

## 🕵️ Security & Audit
Standard auditing protocols for this project.

1.  **Python Vulnerabilities (Hash-Verified):**
    ```bash
    docker-compose exec backend python scripts/security_audit.py
    ```
    *Runs `pip-audit --require-hashes` against `backend/requirements.lock`. Strict cryptographic verification. No warnings.*

2.  **Regenerate Lockfile** (after `requirements.txt` changes):
    ```bash
    docker-compose exec backend pip-compile requirements.txt --generate-hashes -o requirements.lock
    ```
    *Must be committed alongside any `requirements.txt` change.*

3.  **Frontend Vulnerabilities:**
    ```bash
    cd frontend && pnpm audit
    ```
    *Scans npm dependency tree. Fix high-severity issues promptly.*

4.  **Container Vulnerabilities:**
    ```bash
    docker scout quickview vectorbox-backend
    ```

---
**Last Updated:** 2026-05-10

## VBS scoring & embedding refresh — 2026-05 cookbook

After the VBS v2 formula and embedding overhaul (`include_title=False`, `cinematic_description` text override), the canonical refresh sequence is:

```bash
# 1. (optional) Drain OMDb daily quota for fresh ratings on stale rows
docker compose exec backend python scripts/maintenance_orchestrator.py --phases 1 --omdb-budget 1000

# 2. Recompute VBS for the whole catalogue with the new formula (no API hits)
docker compose exec backend python scripts/recalc_vbs_from_db.py

# 3. Re-embed the catalogue (no API hits — uses cinematic_description from DB)
docker compose exec backend python scripts/reembed_catalog.py

# 4. Re-cluster every user — clusters depend on the new vector space
docker compose exec backend python scripts/reset_profiles.py --force

# 5. Flush Redis so feeds pick up the new vectors immediately
docker compose exec backend python -c "
import asyncio, os
import redis.asyncio as aioredis
async def f():
    r = aioredis.from_url(os.getenv('REDIS_URL', 'redis://redis:6379'), decode_responses=True)
    try:
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, count=100)
            if keys: await r.delete(*keys)
            if cursor == 0: break
    finally:
        await r.close()
asyncio.run(f())
"

# 6. (optional) Run Phases 3+4 to re-enrich films flagged with low embedding_quality_score
docker compose exec backend python scripts/maintenance_orchestrator.py --phases 3,4 --embed-limit 500
# Then loop back to step 3 to consolidate the new descriptions into vectors.
```

Step 1 is the only one that hits external APIs (OMDb). Steps 2-5 are local and take a few minutes total.
