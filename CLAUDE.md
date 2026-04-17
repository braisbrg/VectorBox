# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## VectorBox — Project Context

### Stack
- **Backend:** FastAPI + SQLAlchemy async + PostgreSQL 15 + Qdrant + Redis 7 + Python 3.11
- **Frontend:** Next.js 16 App Router + React 19 + Tailwind v4
- **Auth:** httponly cookie `vectorbox_token` (primary) + Bearer header (SSR fallback)
- **Dev:** Docker Compose, Windows 11 PowerShell host, Uvicorn auto-reloads on save
- **Branch:** feature/* → develop → main (tags semver)

### Critical rules — violations break production

**ID types — never confuse**
- `Movie.id` = PostgreSQL internal ID
- `Movie.tmdb_id` = Qdrant point ID  
- `seen_ids` ALWAYS uses `tmdb_id` — NEVER `movie.id`
- NEVER pass `movie.id` to Qdrant

**SQLAlchemy**
- ALWAYS `.is_(True)` / `.is_(False)` — NEVER `== True` / `== False`
- Background tasks ALWAYS create their own `AsyncSessionLocal()`
- NEVER share AsyncSession across parallel tasks

**Services**
- NEVER instantiate `TMDBClient()`, `QdrantService()`, `EmbeddingService()` inside route handlers or loops
- Use `Depends()` for singletons in route handlers
- `generate_embedding()` is CPU-bound → always `run_in_executor`

**Git workflow**
- ALWAYS branch from `develop` — never from `main`
- Branch naming: `feature/description-kebab-case`
- After completing work: merge to `develop`, push, delete the feature branch
- NEVER push directly to `main` — only via develop
- CLAUDE.md must exist in every branch — if missing, copy from develop before doing anything else
- After any `git checkout` or `git pull`, verify CLAUDE.md exists: `ls CLAUDE.md`

**Redis**
- `aioredis.from_url()` is SYNC in redis-py >= 4.2 — NEVER `await` it
- Always `await r.close()` in a `finally` block
- `FEED_CACHE_VERSION` defined in `config.py` — single source of truth

**Security**
- `user_id` ALWAYS from `get_current_user` — NEVER from request body
- Every endpoint touching user data needs `Depends(get_current_user)`

### Key constants
- `FEED_CACHE_VERSION` → `config.py`
- `MIN_QUALITY_SCORE = 55` → `recommendation_engine.py`
- `normalize_similarity_score()` → `utils/scoring.py` — never inline

### Key files (read before touching)
backend/
config.py                    — FEED_CACHE_VERSION, AsyncSessionLocal, REDIS_URL
dependencies.py              — get_current_user, singletons via Depends
services/feed_service.py     — get_main_feed, SECTION_CACHE_TTLS
services/recommendation_engine.py  — Trident signals, anchor selection
services/recommendation_service.py — hybrid_reranking, RRF
services/clustering_service.py     — K-Means, medoid, Groq LLM naming
services/data_processor.py         — ZIP parser (_get_key: year float→int)
utils/scoring.py                   — normalize_similarity_score()
frontend/
types/feed.ts               — Contributor interface
lib/api.ts                  — FeedItem, getTMDBImageUrl
components/right-console.tsx — Inspector, Why This Film

### Current version: v1.7.3
**What's stable:** Security audit complete (3 rounds), optimization sprints done, ingestion pipeline fixed (is_liked, watch_count, ZIP order), Trident engine tuned. FEED_CACHE_VERSION canonical source in `config.py`. fastapi-cache2 decorators removed from AsyncSession methods. Singleton injection enforced.

**Active issues:**
- Inspector: poster image not loading
- Inspector: "Why This Film" shows only signal label, not real contributor data
- Inspector: VectorBox score not expandable
- Clusters: off-genre movies appearing in sections despite genre filter

**Next agreed work:**
1. Inspector UI fixes (poster, contributors, score breakdown)
2. Data flow audit
3. Upload waiting page (task_id + task_store exist in backend)

### Useful commands
```powershell
# Flush Redis (uses SCAN to avoid blocking O(N) KEYS)
docker compose exec backend python -c "
import asyncio, os
import redis.asyncio as aioredis
async def flush():
    r = aioredis.from_url(os.getenv('REDIS_URL', 'redis://redis:6379'), decode_responses=True)
    try:
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, count=100)
            if keys:
                await r.delete(*keys)
            if cursor == 0:
                break
    finally:
        await r.close()
asyncio.run(flush())
"

# Re-cluster
docker compose exec backend python scripts/reset_profiles.py

# Import check
docker compose exec backend python -c "import services.recommendation_engine; print('OK')"

# Frontend rebuild
docker compose up -d --build frontend
```