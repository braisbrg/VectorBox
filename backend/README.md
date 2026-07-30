# VectorBox — Backend

FastAPI service behind the recommender: ingestion, the trident engine, Magic Box
search, and the feed. Python 3.11, async throughout.

## Running it

The backend is not meant to be run on its own — it needs Postgres, Qdrant and
Redis, and the compose file wires all four together:

```bash
docker compose up -d          # from the repository root
```

⚠ **Uvicorn auto-reload does not fire on host-side edits** on Windows: bind-mount
file events never reach the container watcher. After any change to backend code:

```bash
docker compose restart backend
```

If feed logic changed, also flush the `section:*` Redis keys — the cache-save
block refreshes TTLs on cache-hit sections, so stale content self-perpetuates.

## Tests

```bash
docker compose exec backend python -m pytest -q
```

The default run is hermetic: no Groq, no TMDB, no OMDb. The handful of tests that
need a live service are marked `integration` and deselected by `pytest.ini`; run
them with `-m integration` when you actually want to spend the quota.

Three of them are contract guards worth knowing about, because each closes a class
of bug that fails **silently** — something declared that never gets applied:

| test | what it refuses to let happen |
|---|---|
| `test_qdrant_filter_contract` | a filter key written but never handled, so the search runs without the constraint |
| `test_qdrant_payload_writers` | a payload writer that omits a key, which DELETES it from the point |
| `test_no_dead_parameters` | a parameter accepted and never read, so every caller passing it is ignored |

## Verifying search behaviour without spending LLM budget

```bash
docker compose exec backend python scripts/verify_search_branches.py --repeat 3
```

`forced_intent` bypasses the parser, so every downstream decision is
deterministic and repeatable on zero quota. Prefer it to anything that measures
*through* the LLM — see the "Measuring" section of the project guide for why.

For the parser itself there is `scripts/audit_search.py`, a 41-query panel that
runs the real route body. It paces itself at 20s/query because Groq's free tier
caps at **8000 tokens per minute** — not per day — and a parse costs ~2000.

## Where the moving parts live

| area | file |
|---|---|
| Trident signals, anchors | `services/recommendation_engine.py` |
| Hybrid re-ranking, RRF | `services/recommendation_service.py` |
| Magic Box intent parsing | `services/nlp_search.py` |
| Groq model chains | `services/llm_models.py` — the only file to edit when a model dies |
| VBS formula | `services/omdb_client.py` |
| Feed assembly + caching | `services/feed_service.py` |
| Canonical Qdrant payload | `models/external_schemas.py` |

Scripts are catalogued in `docs/SCRIPTS_GUIDE.md`.

## Environment

See `.env.example` at the repository root. `HF_TOKEN` is required at first launch
(the embedding model is gated), and `GROQ_API_KEY` powers both the enrichment and
the Magic Box parser.

## Security posture

Rate limiting (slowapi, Redis-backed), strict CORS and trusted-host middleware,
Pydantic validation at every boundary, prompt-injection screening on free text,
upload sanitisation, security headers, non-root container, and SQLAlchemy ORM
throughout. `user_id` always comes from the authenticated session, never from a
request body.
