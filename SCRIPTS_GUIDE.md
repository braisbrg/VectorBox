# VectorBox Scripts Guide

A comprehensive inventory of maintenance, security, and utility scripts for the internal DevOps / Architecture team.

## 🐍 Backend Maintenance Scripts
All Python scripts located in `backend/scripts/`. Run these via Docker execution.

| Script | Description | Command (Safe to Run) |
| :--- | :--- | :--- |
| **`maintenance_orchestrator.py`** | **Master Orchestrator.** Runs the full DB maintenance pipeline in 8 phases respecting OMDb daily budget (`api_budget` table) and Groq daily limits. Phases: (1) refresh_metadata for missing `imdb_vote_count` or stale, (2) embedding_audit for NULL `embedding_quality_score`, (3) embedding_repair via Groq for low-quality / not-yet-enriched, (4) backfill cinematic descriptions, (5) reset user clusters, (6) recalc VBS from DB columns (no API — catches films Phase 1 didn't refresh and propagates formula changes), (7) vector_presence_check — diffs DB vs Qdrant point IDs and re-upserts the missing ones from stored text (replaces legacy `heal_vectors.py`), (8) popular_refresh — scrapes Letterboxd "Popular This Week" with Trakt fallback and writes the Redis cache that powers the "Popular on Letterboxd" feed section (replaces legacy `popular_scraper.py`). Stops gracefully on budget exhaustion. Resumable across runs. | `docker-compose exec backend python scripts/maintenance_orchestrator.py [--phases 1,2,3,6,7,8] [--omdb-budget 1000] [--embed-limit 500] [--dry-run]` |
| **`seed_db.py`** | **The Main Engine.** Uses `MovieFactory` to fetch movies from TMDB with **Spanish Metadata**, **Keywords**, and strict Pydantic **OMDb Ratings**. Upserts to Postgres + Qdrant. 12 discovery strategies covering TMDB Discover sorts, language slicing, Trakt user-behaviour lists, production companies, and saga collections. See `--strategy` details below. | `docker-compose exec backend python scripts/seed_db.py --limit 100 --strategy popular` |
| **`seed_essentials.py`** | **Curated Bootstrap.** Iterates a hardcoded list of canonical companies (Marvel, Pixar, Ghibli, Disney, A24, Warner…) and sagas (Star Wars, Harry Potter, MCU phases, James Bond, LOTR, Pixar collections…) and seeds any film not yet in DB. Idempotent. Reuses a single `DatabaseSeeder` instance so the embedding model loads once. Use to fill in the canonical cinema after the catalogue has been bootstrapped with `popular`. | `docker-compose exec backend python scripts/seed_essentials.py` |
| **`refresh_metadata.py`** | **Metadata Refresher.** Fetches fresh `vote_count`, `vote_average`, `popularity`, `poster_path`, `genres`, `runtime` from TMDB and recalculates `vectorbox_score` for movies already in DB. Selects movies by age cohort. Use `--dry-run` to preview. | `docker-compose exec backend python scripts/refresh_metadata.py --strategy recent --limit 200` |
| **`enrich_vectors.py`** | **Data Fixer & LLM Embeddings.** Fetches missing keywords/credits from TMDB. Uses Groq to generate ~80-word cinematic descriptions and upserts 768d semantic vectors (`google/embeddinggemma-300m`). Run with `--enrich-embeddings` to process LLM upgrades. Model control: `--model-only <alias>` (single model, no fallback; aliases `gemini\|70b\|8b\|oss-120\|oss-20\|qwen3-32b\|qwen3.6-27b`), `--chain a,b` (ordered fallback — the 2026-06 sweep favours `qwen3-32b,oss-120`), `--parallel` (run a `--chain` concurrently), `--smart` (top-4 models), and `--reset-enrichment` for a fresh start. | `docker-compose exec backend python scripts/enrich_vectors.py --enrich-embeddings --chain qwen3-32b,oss-120 [--parallel]` |
| **`backfill_descriptions.py`** | **Description Backfiller.** Fills `cinematic_description` for movies that already have LLM embeddings (`has_enriched_embedding=True`) but no saved description. Does NOT regenerate embeddings — only calls the LLM and saves the text. Handles `DailyLimitExhausted` gracefully (commits progress and stops). Use `--dry-run` to count. | `docker-compose exec backend python scripts/backfill_descriptions.py [--limit N] [--dry-run]` |
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
| **`migrate_release_dates.py`** | **Schema Migration.** One-time migration that adds the `release_dates JSONB` column to the `movies` table. Safe to re-run (`ADD COLUMN IF NOT EXISTS`). | `docker-compose exec backend python scripts/migrate_release_dates.py` |
| **`verify_qa_pt2.py`** | **QA Certification (Phase 5).** End-to-end HTTP check against a running stack: logs in as `qa_vecbox` and validates the `/api/recommendations/feed` response. | `docker-compose exec backend python scripts/verify_qa_pt2.py` |
| **`debug_movie.py`** | **Diagnostic.** Inspects a single movie: DB metadata, top-10 Qdrant vector neighbors, and (with `--user-id`) signal-by-signal eligibility analysis (Signal A anchor similarity, Auteur director rank, Signal C TMDB rec presence). Lookup by title (fuzzy) or `--tmdb-id`. | `docker-compose exec backend python scripts/debug_movie.py "Spirited Away" --user-id 212` |
| **`recalc_vbs_from_db.py`** | **VBS Backfill (no API).** Recomputes `vectorbox_score` for every movie using existing DB columns (`imdb_rating`, `metacritic_rating`, `vote_average`, `imdb_vote_count`, `vote_count`) — **does NOT hit OMDb**. Run this after any change to the VBS formula in `omdb_client.py` to consolidate the catalogue in seconds. Reports updated/cleared/unchanged counts and average delta. | `docker compose exec backend python scripts/recalc_vbs_from_db.py` |
| **`reembed_catalog.py`** | **Bulk Re-embedding.** Regenerates Qdrant vectors for the entire catalogue using `cinematic_description` (preferred, ~99% coverage) or `overview + genres + keywords` (fallback). NEVER includes title — title-token leakage causes off-theme BYW/Magic Box neighbours. Model is `google/embeddinggemma-300m` (768-dim, gated HF — needs `HF_TOKEN`) and Qdrant collection `movies` is recreated at 768-dim if dimension mismatch is detected. After running, re-cluster with `reset_profiles.py --force` and flush Redis. ~10-20 min for 7500 films. No API hits. | `docker compose exec backend python scripts/reembed_catalog.py` |
| **`delete_collection.py`** | **Qdrant Wipe.** Drops the entire `movies` collection in Qdrant. Used as a prerequisite when changing vector dimension (e.g. MiniLM 384 → embeddinggemma 768). Destructive — always pair with `reembed_catalog.py` immediately after. | `docker compose exec backend python scripts/delete_collection.py` |
| **`apply_qdrant_quantization.py`** | **INT8 Quantization (deferred).** Enables scalar INT8 quantization on the `movies` collection (`always_ram=True`, rescoring=True). Idempotent. NOT applied today — at <10k points the ~9MB RAM saving doesn't justify 1-3% recall loss. Revisit at >100k points or once a golden-set recall@10 benchmark exists. | `docker compose exec backend python scripts/apply_qdrant_quantization.py` |
| **`experiment_embeddings_recipe.py`** | **2×2 Embedding × Text Ablation.** Isolates whether wins come from the model (embeddinggemma vs MiniLM) or the source text (`cinematic_description` vs `overview+genres+keywords`). 4 variants on the same 7-anchor pool as `experiment_embeddings.py`. Use when a benchmark improves but it's unclear which knob did the work. | `docker compose exec backend python scripts/experiment_embeddings_recipe.py` |
| **`experiment_signal_a.py`** | **Signal A centroid strategies.** Compares 7 strategies for the "Picked For You" Vibe vector (global centroid, medoid mean, per-cluster equal/weighted, quality-weighted, top-quality slice, multi-anchor RRF). Reports top-15 + intra-list diversity (mean pairwise cosine distance). Run before tuning Signal A for users with multi-cluster tastes. | `docker compose exec backend python scripts/experiment_signal_a.py --user 212 --limit 15` |
| **`experiment_feed_sections.py`** | **Cluster-vs-ratings derivation A/B.** For 4 feed sections (niche_picks, hidden_gems, wildcard, upcoming) compares the current cluster-derived signal vs a rating-aggregated equivalent (no cluster indirection). Use to evaluate whether removing cluster dependency simplifies the section without losing quality. | `docker compose exec -e PYTHONPATH=/app backend python scripts/experiment_feed_sections.py --user 212` |
| **`synthetic_profiles.py`** | **F-23 Synthetic taste profiles.** Builds 6 throw-away users with extreme opinionated taste (gangster, horror, plot_twist, family, quinqui, french_arthouse), 12-14 5★ anchors each. Triggers clustering and asserts top recommendations stay in-genre. Pass `--cleanup` to delete every `synthetic_*` user (CASCADE wipes ratings). Use after any ranking/embedding/VBS change to eyeball regressions a unit test can't catch. | `docker compose exec backend python scripts/synthetic_profiles.py [--cleanup]` |
| **`test_magic_search_per_profile.py`** | **F-23 Test #1.** For each synthetic user, runs a panel of representative queries through the full Magic Search pipeline (intent → embedding → Qdrant → Sprint 1+2 filters → Sprint 3 blend + re-sort), excluding the user's own anchors. Prints top results with vector / VBS / blended score. | `docker compose exec backend python scripts/test_magic_search_per_profile.py` |
| **`test_feed_per_profile.py`** | **F-23 Test #2.** Calls `FeedService.get_main_feed` for each synthetic user and prints the top 4 picks per section. Catches regressions in any engine signal (Vibe, Auteur, Hidden Gems, Niche Picks, Available Now) that a single Magic Search query wouldn't surface. | `docker compose exec backend python scripts/test_feed_per_profile.py` |
| **`test_f21_fallback.py`** | **F-23 Test #3.** Verifies the F-21 dynamic-threshold fallback fires when a niche theme + synthetic user combination has too few qualifying films. Captures the engine logger and reports section size + whether the fallback widened the gate (`RELAXED` vs `ok`). | `docker compose exec backend python scripts/test_f21_fallback.py` |
| **`validate_magic_search.py`** | **Magic Search e2e validation.** Runs ~10 representative queries through the exact API pipeline (`parse_user_intent` → embedding → Qdrant → Sprint 1+2 DB post-filter → Sprint 3 blend + sort) and prints the top results with scores. Lets you eyeball ordering changes without a frontend. Override the query set with `MAGIC_QUERIES="q1\|q2\|q3"`. | `docker compose exec backend python scripts/validate_magic_search.py` |
| **`post_change_smoke.py`** | **Post-Deploy Smoke Test.** One-shot sanity pass after `docker compose up -d --build` or after a deploy: embedding model alignment, catalog coverage, VBS distribution, user integrity (B-18/B-20), Qdrant golden-set recall, `zip_uploads` table. Green / yellow / red per check. NOT a replacement for pytest — this is the "did production survive my changes?" eyeball pass. | `docker compose exec backend python scripts/post_change_smoke.py` |
| **`check_ann_recall.py`** | **HNSW health gate.** For each of the 7 curated anchors, runs the same query against Qdrant twice (`hnsw_ef=128 exact=False` vs `exact=True`) and computes `recall@k` from the overlap. Target ≥ 0.95. At current scale (~13.5k × 768-dim) mean recall is ~1.0 — the script is preventive, becomes load-bearing when the catalog grows past ~50k points. Exit 0 if pass, 1 if fail (CI-ready). | `docker compose exec backend python scripts/check_ann_recall.py [--k 10] [--hnsw-ef 128]` |
| **`check_query_asymmetry.py`** | **Embedding asymmetry probe.** Re-encodes each anchor three ways — STORED vector, re-encoded from `cinematic_description`, re-encoded from title alone — and counts hits@k against TST-2's curated neighbours. Confirms (1) catalog pipeline consistency (STORED ≡ DESCR ⇒ no embedding drift) and (2) how load-bearing the LLM query-expansion is for short Magic Search queries. Current production: 46% drop title-only vs stored, so expansion + title-boost gate are both necessary. Re-run after any embedding-model or prompt change. | `docker compose exec backend python scripts/check_query_asymmetry.py [--k 10\|20]` |
| **`cleanup_anonymous_users.py`** | **Nightly Cron.** Deletes anonymous users inactive for ≥N days (default 90). CASCADE on `user_ratings` FK handles related cleanup. `--dry-run` previews, `--days N` overrides the threshold. | `docker compose exec backend python scripts/cleanup_anonymous_users.py [--dry-run] [--days 90]` |
| **`experiment_embeddings.py`** | **Embedding A/B Comparison.** Curated 80-film pool with 7 anchors (Howl's, Deprisa Deprisa, Pan's Labyrinth, Inception, Godfather, Goodfellas, Spirited Away) and known thematic neighbours. Compares 4-5 embedding variants (current Qdrant baseline, MiniLM-no-title, multilingual-MiniLM, multilingual-e5-small, optionally EmbeddingGemma if HF token present) on top-8 hit rate. Output is a tabular qualitative report — use to decide before any model change or major re-embedding. | `docker compose exec backend python scripts/experiment_embeddings.py` |
| **`experiment_signal_c.py`** | **Signal C filter A/B.** Curated 12-film pool (4 niche + 4 art-house + 4 popular). For each seed fetches both TMDB endpoints (`/recommendations` and `/similar`), computes vector cosine + genre overlap vs the seed, and tabulates which candidates would survive each filter strategy (raw, vec≥0.45, vec≥0.50, genre-only, combos). Reports aggregate pass-rate per strategy across all seeds + multi-seed agreement analysis for `user_id=212`. Use before tuning `SIGNAL_C_VEC_SIM_THRESHOLD` or before swapping data sources. | `docker compose exec backend python scripts/experiment_signal_c.py` |
| **`experiment_enricher_prompt.py`** | **Prompt × model A/B for cinematic_description.** Generates descriptions for a curated 25-film panel (art-house, mainstream, classic, Spanish, Asian, horror, cult, sci-fi) across 6 combos: 2 prompts (current production prompt vs new prompt with country/era/awards/few-shot/anti-cliché) × 4 models (Scout, 70B, gpt-oss-120b, gpt-oss-20b — note Scout was decommissioned 2026-07-17, so that column no longer runs). Handles rate limits gracefully: skips models that exhaust their daily quota mid-run, retries once on per-minute 429. Output: markdown file with all combos side-by-side per film + the currently-stored description for reference. No DB writes. Use to validate any change to the enrichment prompt or chain before bulk re-enrichment. Flags: `--limit N` (quick dry-run), `--delay 0.5` (inter-call pacing), `--output PATH`, `--with-8b` (swap oss-20 for 8b). | `docker compose exec backend python scripts/experiment_enricher_prompt.py --limit 3` |
| **`experiment_enricher_prompts_v2.py`** | **Prompt-only A/B (model fixed = GPT-OSS-120B).** Pins the strongest enrichment model and varies ONLY the prompt across 6 variants: `V0-current` (production), `V1-new-rich` (country/era/awards/few-shot), `V2-new-nameban` (V1 + ban on naming director/cast/title in OUTPUT, to avoid token-leakage like the title-leakage we already fixed), `V3-minimal-vibes` (strip inputs, vibes-only), `V4-keyword-anchored` (instruct LLM to use TMDB keywords as the prose spine), `V5-no-mood-tail` (V2 without isolated mood-keyword tail). Reuses helpers from `experiment_enricher_prompt.py`. Same 24-film panel for cross-comparability. Flag `--reasoning-effort low\|medium\|high` controls gpt-oss internal CoT budget — useful when empty responses appear (model exhausting `max_tokens` on internal reasoning). Output: markdown side-by-side. No DB writes. | `docker compose exec backend python scripts/experiment_enricher_prompts_v2.py --reasoning-effort low` |
| **`experiment_reasoning_effort.py`** | **Reasoning-effort A/B for GPT-OSS-120B.** Locks model + prompt (V2-new-nameban) and varies ONLY `reasoning_effort` across `low`/`medium`/`high`. Sample of 8 films keeps token cost modest. Use to diagnose whether empty responses come from over-reasoning vs the prompt itself. Once you know the best level, pass it via `--reasoning-effort` to `experiment_enricher_prompts_v2.py` or to the production `cinematic_enricher.py`. | `docker compose exec backend python scripts/experiment_reasoning_effort.py` |
| **`experiment_cluster_naming.py`** | **Scout vs 70B for cluster naming.** _(Historical — this is the A/B that informed cluster naming; Scout was decommissioned 2026-07-17, production now uses `qwen/qwen3-32b`.)_ `clustering_service.py` asks an LLM to label a cluster of films in 2-4 words (e.g. "Studio Ghibli Wonder", "Korean Revenge Cinema"). This script runs the SAME production prompt against both models on 6 synthetic-but-realistic clusters (Ghibli theme / slow-burn mood / Korean revenge subgenre / Tarantino style / mixed blockbusters / 80s synth sci-fi). Output: markdown side-by-side. Use to decide whether 70B is good enough here or whether Scout deserves to be primary for naming too. | `docker compose exec backend python scripts/experiment_cluster_naming.py` |
| **`experiment_magicbox_parser.py`** | **Scout vs 70B for Magic Box intent parsing.** _(Historical — Scout decommissioned 2026-07-17; the parser is now 70B primary → `qwen/qwen3-32b` fallback.)_ `nlp_search.parse_user_intent` converts a free-text query into a structured `MovieSearchIntent` (Pydantic via instructor). This script runs the SAME parser against both models on 12 realistic queries covering era / language / regional cinema / reference-film / mood / awards / hidden-gem dimensions. Output: markdown with JSON side-by-side per query. Use to decide whether 70B's stricter schema adherence is worth the trade vs Scout's prose strength. | `docker compose exec backend python scripts/experiment_magicbox_parser.py` |
| **`experiment_trakt.py`** | **Alternative Signal C source comparison.** Same 12-seed pool as `experiment_signal_c.py` but pulls related films from **Trakt API** (`/movies/{id}/related`) instead of TMDB. Use to evaluate whether Trakt's user-behaviour-based recs are higher quality than TMDB's noisy collab filter, especially for niche/recent/non-English films. Requires `TRAKT_CLIENT_ID` env var (free, sign up at https://trakt.tv/oauth/applications). Reports catalogue-coverage (% of recs already in our DB) and pass-rate per filter strategy. | `docker compose exec backend python scripts/experiment_trakt.py` |
| **`check_embeddings.py`** | **Embedding Sanity Check.** Compares stored Qdrant vectors against an embeddinggemma reference embedding built from the shared name-free recipe (`overview + genres + keywords` — see `backend/utils/embedding_reference.py`). Flags movies below cosine threshold as likely-corrupt. Flags: `--update-db` persist score, `--fix` re-enrich flagged, `--user-id` scope to one user, `--tmdb-id` check a single movie, `--recheck` re-run on movies that already have a score (default skips them), `--verbose` print reference text + first 5 dims of stored/reference vectors, `--threshold` let's you change the threshold value to flag movies | `docker-compose exec backend python scripts/check_embeddings.py --tmdb-id 129 --verbose --recheck --threshold 0.5` |
| **`fix_qdrant_ids.py`** | **Qdrant ID Audit (T-04).** Walks every Qdrant point and classifies it as modern (`point.id == Movie.tmdb_id`), legacy (`point.id == Movie.id`, requires migration to tmdb_id), or orphan (no DB record). Migrates legacy points by re-upserting under the correct tmdb_id and deleting the legacy point. Dry-run by default; pass `--execute` to apply. `--delete-orphans` (requires `--execute`) wipes points with no DB record. | `docker-compose exec backend python scripts/fix_qdrant_ids.py [--execute] [--delete-orphans] [--limit 200]` |
| **`test_guest_feed.py`** | **Recommendation Quality QA.** Tests the `/public/guest-feed` recommendation logic offline. Accepts a JSON ratings dict, a DB user ID, or a named preset (`cinephile`, `blockbuster`). Reports VectorBox score distribution, genre distribution, top-10 results, and genre coverage (% of positive-seed genres represented in recs). | `docker compose exec backend python scripts/test_guest_feed.py --preset cinephile` |

### seed_db.py — `--strategy` details

Every strategy dedupes against existing `Movie.tmdb_id` before processing, so re-runs cost only TMDB pagination + 1 detail call per genuinely-new film.

| Strategy | Source | Sort / Filter | Use case |
| :--- | :--- | :--- | :--- |
| `popular` *(default)* | TMDB Discover | `vote_count.desc`, `vote_count≥50` | Bulk seed with well-known films |
| `recent` | TMDB Discover, last 90 days | `primary_release_date.desc`, `vote_count≥20` | Keep DB current with new releases |
| `upcoming` | TMDB Discover, next 180 days | `popularity.desc`, no vote floor | Seed upcoming films, sets `is_upcoming=True` + fetches per-country release dates |
| `top_rated` | TMDB Discover | `vote_average.desc`, `vote_count≥1500` | Critics' favorites — high-bar vote count filters out obscure 10/10s |
| `by_language` | TMDB Discover | `with_original_language=<iso>` + `vote_count.desc`, `vote_count≥30` | Combat anglo bias. Requires `--language es\|ja\|ko\|fr\|de\|it\|...` |
| `classic` | TMDB Discover, pre-1990 | `vote_count.desc`, `vote_count≥100` | Old cinema that vote_count.desc on the global pool drowns out |
| `trending` | TMDB `/trending/movie/week` | TMDB internal trending score | What's hot this week (small pool, ~60 films) |
| `trakt_popular` | Trakt `/movies/popular` | most-watched globally | User-behaviour signal vs TMDB vote count — distinct sesgo, cinéfilo |
| `trakt_trending` | Trakt `/movies/trending` | most users watching right now | Real-time engagement — newer/buzzier than TMDB trending |
| `trakt_anticipated` | Trakt `/movies/anticipated` | most added to watchlists (unreleased) | Better signal than `upcoming` — filtered by *demand*, not raw popularity. Also marks `is_upcoming=True` + fetches per-country release dates (same as TMDB `upcoming`). |
| `by_company` | TMDB Discover, `with_companies` | `vote_count.desc`, no vote floor | All films of a production company. Requires `--company-id <N>`. |
| `by_collection` | TMDB `/collection/{id}` | Single call, returns all parts | Enumerate every film of a saga. Requires `--collection-id <N>`. `--limit` ignored. |

**Requirements:**
- Trakt strategies need `TRAKT_CLIENT_ID` in env (free at https://trakt.tv/oauth/applications). Already configured for Signal C.
- `by_language` errors out without `--language`.
- `by_company` errors out without `--company-id`.
- `by_collection` errors out without `--collection-id`.

**Canonical IDs reference** (use with `by_company` / `by_collection`):

| Production company | TMDB ID | | Saga / Collection | TMDB ID |
| :--- | :---: | :--- | :--- | :---: |
| Marvel Studios | 420 | | Star Wars | 10 |
| Lucasfilm | 1 | | James Bond | 645 |
| Pixar | 3 | | Harry Potter | 1241 |
| Walt Disney Pictures | 2 | | Lord of the Rings | 119 |
| Walt Disney Animation | 6125 | | The Hobbit | 121938 |
| Studio Ghibli | 10342 | | Pirates of the Caribbean | 295 |
| DreamWorks Animation | 521 | | Jurassic Park | 328 |
| A24 | 41077 | | Indiana Jones | 84 |
| Laika | 6194 | | Mission: Impossible | 87359 |
| New Line Cinema | 12 | | John Wick | 404609 |
| Warner Bros. | 174 | | Fast & Furious | 9485 |
| Universal Pictures | 33 | | The Matrix | 2344 |
| 20th Century Studios | 25 | | Terminator | 528 |
| MGM | 21 | | Alien | 8091 |
| | | | Predator | 399 |
| | | | Mad Max | 8945 |
| | | | Back to the Future | 264 |
| | | | The Avengers (MCU) | 86311 |
| | | | Toy Story | 10194 |
| | | | Inside Out | 86029 |
| | | | Shrek | 2150 |
| | | | The Godfather | 230 |
| | | | Avatar | 87096 |

For the full canonical list see `backend/scripts/seed_essentials.py`. Browse more at https://www.themoviedb.org/collection or `/company`.

```bash
# TMDB strategies
docker-compose exec backend python scripts/seed_db.py --strategy popular --limit 500
docker-compose exec backend python scripts/seed_db.py --strategy recent --limit 200
docker-compose exec backend python scripts/seed_db.py --strategy upcoming --limit 100
docker-compose exec backend python scripts/seed_db.py --strategy top_rated --limit 500
docker-compose exec backend python scripts/seed_db.py --strategy by_language --language ja --limit 300
docker-compose exec backend python scripts/seed_db.py --strategy classic --limit 500
docker-compose exec backend python scripts/seed_db.py --strategy trending --limit 60

# Trakt strategies (require TRAKT_CLIENT_ID)
docker-compose exec backend python scripts/seed_db.py --strategy trakt_popular --limit 500
docker-compose exec backend python scripts/seed_db.py --strategy trakt_trending --limit 200
docker-compose exec backend python scripts/seed_db.py --strategy trakt_anticipated --limit 200

# Companies and collections
docker-compose exec backend python scripts/seed_db.py --strategy by_company --company-id 420 --limit 100      # Marvel Studios
docker-compose exec backend python scripts/seed_db.py --strategy by_company --company-id 10342 --limit 50    # Studio Ghibli
docker-compose exec backend python scripts/seed_db.py --strategy by_collection --collection-id 10            # Star Wars
docker-compose exec backend python scripts/seed_db.py --strategy by_collection --collection-id 1241          # Harry Potter

# Bootstrap canonical cinema in one shot (idempotent)
docker-compose exec backend python scripts/seed_essentials.py
```

### maintenance_orchestrator.py (master)

Single entry point for routine DB maintenance. Replaces ad-hoc sequencing of `refresh_metadata`, `check_embeddings`, `enrich_vectors`, `backfill_descriptions`, and `reset_profiles`.

**Phases (run in order; filter with `--phases`):**

| # | Name | API used | Stop condition |
|---|---|---|---|
| 1 | `refresh_metadata` | OMDb + TMDB | OMDb daily budget reached |
| 2 | `embedding_audit` | none (local embeddinggemma) | `--embed-limit` |
| 3 | `embedding_repair` | Groq | `DailyLimitExhausted` or `--embed-limit` |
| 4 | `backfill_descriptions` | Groq | `DailyLimitExhausted` or `--embed-limit` |
| 5 | `reset_profiles` | none | runs once over every user with at least one rating |
| 6 | `recalc_vbs` | none | runs once over the whole `movies` table |
| 7 | `vector_presence_check` | none | runs once; re-encodes & upserts films missing from Qdrant |
| 8 | `popular_refresh` | Letterboxd HTML + Trakt fallback | one scrape; writes `cache:{FEED_CACHE_VERSION}:popular_letterboxd:ids` with 24h TTL |

**Arguments:**
- `--phases 1,2,3,4,5,6,7,8` — comma-separated phases to run (default: all)
- `--omdb-budget N` — max OMDb calls for this run, capped by remaining daily quota in `api_budget` table (default: 100000 — Patron tier)
- `--embed-limit N` — max movies per **Groq-bound** embedding phase (3 repair, 4 backfill). Default: 500. These cost Groq quota so the cap is conservative.
- `--audit-limit N` — max movies per Phase 2 audit (no API — only local embeddinggemma inference, ~15ms/film). Default: 20000 ≈ 2× current catalog. Raise if seeds push the catalog past ~18k. A partial sweep is now flagged with a `STILL UNAUDITED: N` WARNING so it's visible.
- `--dry-run` — preview targets without writing

**Phase 1 selector:** films with `imdb_id` set AND (NULL `imdb_vote_count` OR `last_metadata_refresh` older than `REFRESH_STALE_DAYS` = 7d). Ordered by `popularity DESC`. With Patron tier + ~10k catalog a full weekly sweep ≈ 10k calls (10% of daily cap), so the constant is dimensioned for weekly freshness, not the old monthly cadence.

**Phase 8 source-of-truth:** Letterboxd's `/films/ajax/popular/this/week/` endpoint, scraped via `curl_cffi` Chrome TLS impersonation. Slug→tmdb_id resolution uses the Redis cache `letterboxd:slug2tmdb:{slug}` (30d positive / 7d negative). If the scrape resolves < 20 TMDB IDs (parser drift, Cloudflare challenge, 403), falls back to Trakt `/movies/trending` to keep the feed populated. Logs `source=letterboxd|trakt|letterboxd_degraded` so drift is visible.

**Recommended cadence:**
```bash
# Daily — refresh stale metadata, drain Groq quota for repair, recalc VBS,
#         heal any film missing from Qdrant, refresh popular cache.
0 3 * * *  docker compose exec -T backend python scripts/maintenance_orchestrator.py --phases 1,2,3,6,7,8 --omdb-budget 5000 --embed-limit 200

# Weekly — backfill descriptions and re-cluster
0 4 * * 0  docker compose exec -T backend python scripts/maintenance_orchestrator.py --phases 4,5
```

**One-off catch-up (after big formula changes / mass ingest):**
```bash
# Day 1: drain OMDb quota + recalc VBS + heal missing vectors + refresh popular
docker compose exec backend python scripts/maintenance_orchestrator.py --phases 1,6,7,8 --omdb-budget 100000

# Day 2-N: full sweep if anything is still pending
docker compose exec backend python scripts/maintenance_orchestrator.py --phases 2,3,4,5,6,7,8 --embed-limit 1000
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
**Last Updated:** 2026-05-19

## VBS scoring & embedding refresh — 2026-05 cookbook

After the VBS v2 formula and embedding overhaul (`include_title=False`, `cinematic_description` text override), the canonical refresh sequence is:

```bash
# 1. (optional) Drain OMDb daily quota for fresh ratings on stale rows
docker compose exec backend python scripts/maintenance_orchestrator.py --phases 1 --omdb-budget 1000

# 2. Recompute VBS for the whole catalogue with the new formula (no API hits)
#    Standalone script — same logic now also runs as Phase 6 of the orchestrator.
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
