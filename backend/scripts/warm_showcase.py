"""Fill the showcase cache — Fase 1.4 of the landing plan.

`/api/search/showcase` is a pure cache reader: on a miss it returns 503 instead
of computing, which is what keeps the landing's input set closed. Something has
to put the answers there, and this is it.

It drives the ordinary `/api/search/natural` pipeline over HTTP against the
running backend, so the showcase can never drift from what Magic Box itself
would answer — one code path, not two.

Run it on deploy, and after any change to SHOWCASE_QUERIES, the embeddings, or
the ranking:

    docker compose exec backend python scripts/warm_showcase.py
    docker compose exec backend python scripts/warm_showcase.py --dry-run
    docker compose exec backend python scripts/warm_showcase.py --slug grief

The daily Groq budget is 200k tokens; this spends one parse per slug per
language (6 calls for 3 slugs), so it is cheap enough to run on every deploy.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import redis.asyncio as aioredis

from services import showcase_service

BASE_URL = os.getenv("WARM_BASE_URL", "http://localhost:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")


async def warm_one(client: httpx.AsyncClient, redis, slug: str, lang: str, dry: bool) -> bool:
    query = showcase_service.query_for(slug, lang)
    if not query:
        print(f"  ! {slug}/{lang}: sin consulta definida")
        return False

    resp = await client.post(
        "/api/search/natural",
        json={"query": query},
        timeout=60.0,  # a cold parse plus Qdrant; generous on purpose
    )
    if resp.status_code != 200:
        print(f"  ! {slug}/{lang}: HTTP {resp.status_code} — {resp.text[:120]}")
        return False

    data = resp.json()
    results = data.get("results") or []
    intent = data.get("intent") or {}

    # Fase 1.5 — what to do when the run is not healthy.
    #
    # A parser failure still returns results (pure semantic search), but the
    # answer is measurably worse on anything with era or country constraints.
    # Two rules keep a bad run from poisoning the landing for a whole week:
    #
    #   1. Too few results is not "degraded but usable", it is a broken row.
    #   2. A degraded run must never REPLACE a healthy cached answer. Warming
    #      runs on every deploy; without this, one deploy during a Groq outage
    #      would quietly downgrade a page that was working.
    degraded = "All models failed" in str(intent.get("reasoning", ""))

    if len(results) < showcase_service.MIN_RESULTS:
        print(f"  ! {slug}/{lang}: solo {len(results)} resultados "
              f"(mínimo {showcase_service.MIN_RESULTS}), no se cachea")
        return False

    if degraded and not dry:
        existing = await showcase_service.read(redis, slug, lang)
        if existing and not existing.get("degraded"):
            print(f"  = {slug}/{lang}: run degradado, se conserva la entrada sana "
                  f"({len(existing.get('results', []))} resultados)")
            return False

    payload = {
        "slug": slug,
        "lang": lang,
        "query": query,
        "results": results,
        "degraded": degraded,
    }
    if dry:
        print(f"  · {slug}/{lang}: {len(results)} resultados{' (DEGRADADO)' if degraded else ''} "
              f"— {', '.join(m.get('title', '?') for m in results[:3])} … [dry-run]")
        return True

    await showcase_service.write(redis, slug, lang, payload)
    print(f"  ✓ {slug}/{lang}: {len(results)} resultados{' (DEGRADADO)' if degraded else ''} "
          f"— {', '.join(m.get('title', '?') for m in results[:3])} …")
    return True


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="consulta y muestra, sin escribir en Redis")
    ap.add_argument("--slug", help="calentar sólo este slug")
    ap.add_argument("--lang", choices=["es", "en"], help="calentar sólo este idioma")
    args = ap.parse_args()

    slugs = [args.slug] if args.slug else [q["slug"] for q in showcase_service.SHOWCASE_QUERIES]
    langs = [args.lang] if args.lang else ["es", "en"]

    for s in slugs:
        if not showcase_service.is_valid_slug(s):
            print(f"Slug desconocido: {s}. Disponibles: "
                  f"{', '.join(q['slug'] for q in showcase_service.SHOWCASE_QUERIES)}")
            return 2

    print(f"Calentando {len(slugs)} slug(s) × {len(langs)} idioma(s) contra {BASE_URL}"
          f"{' [DRY RUN]' if args.dry_run else ''}\n")

    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    ok = 0
    total = len(slugs) * len(langs)
    try:
        async with httpx.AsyncClient(base_url=BASE_URL) as client:
            for s in slugs:
                for lang in langs:
                    if await warm_one(client, redis, s, lang, args.dry_run):
                        ok += 1
                    # /search/natural is rate limited to 10/minute per IP; the
                    # warm loop is the one caller that would trip it on itself.
                    await asyncio.sleep(7)
    finally:
        await redis.close()

    print(f"\n{ok}/{total} calentados.")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
