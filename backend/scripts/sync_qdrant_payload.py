"""Backfill the payload fields Qdrant needs to filter on — 2026-07-29.

Country, spoken language, certification, Oscar wins and the adult flag were
enforced in Postgres AFTER the vector search returned, because they were not in
the Qdrant payload. That is post-filter starvation: they could only ever subtract
from the twenty nearest neighbours of the query vector, and for a constraint
orthogonal to the theme almost nothing survives. Measured, "thrillers coreanos"
kept ONE film of the 219 Korean ones the catalogue holds.

`_qdrant_payload` writes them now, so every point touched by a re-embed or an
enrichment carries them. This script is for everything written before that —
`set_payload` only, so it never touches a vector and can run on a live system.

    docker compose exec backend python scripts/sync_qdrant_payload.py --dry-run
    docker compose exec backend python scripts/sync_qdrant_payload.py

Run it ONCE after deploying the payload change. Re-running is harmless: it writes
the same values. Verify with scripts/verify_search_branches.py, which needs no
LLM budget.

Related: scripts/recalc_vbs_from_db.py --sync-payload-only does the same job for
vectorbox_score + has_enriched_embedding. Kept separate because that one exists
to propagate a scoring formula change and is run on a different cadence.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie
from services.qdrant_service import QdrantService

BATCH = 500


def payload_for(m: Movie) -> dict:
    """The five fields this script owns. Deliberately NOT the whole payload —
    overwriting title/overview/vectors here would let a stale DB row clobber a
    fresher enrichment."""
    return {
        "countries": m.omdb_countries or [],
        "spoken_languages": m.omdb_languages or [],
        "mpaa_rating": m.mpaa_rating,
        "oscar_wins": m.oscar_wins or 0,
        "is_adult": bool(m.is_adult),
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="cuenta y muestra, sin escribir")
    ap.add_argument("--limit", type=int, help="procesa solo N películas (pruebas)")
    args = ap.parse_args()

    qd = QdrantService()
    await qd.init_payload_indexes()

    async with AsyncSessionLocal() as db:
        q = select(Movie).where(Movie.tmdb_id.is_not(None)).order_by(Movie.id)
        if args.limit:
            q = q.limit(args.limit)
        movies = (await db.execute(q)).scalars().all()

    print(f"{len(movies)} películas en Postgres{' [DRY RUN]' if args.dry_run else ''}\n")

    stats = {"countries": 0, "spoken_languages": 0, "mpaa_rating": 0, "oscar_wins": 0}
    written = 0
    for i in range(0, len(movies), BATCH):
        chunk = movies[i:i + BATCH]
        for m in chunk:
            p = payload_for(m)
            if p["countries"]:
                stats["countries"] += 1
            if p["spoken_languages"]:
                stats["spoken_languages"] += 1
            if p["mpaa_rating"]:
                stats["mpaa_rating"] += 1
            if p["oscar_wins"]:
                stats["oscar_wins"] += 1
            if not args.dry_run:
                # One call per point: set_payload takes a single payload dict, and
                # these values differ per film. 20k small calls against a local
                # Qdrant is a couple of minutes, and this runs once.
                await qd.client.set_payload(
                    collection_name=qd.COLLECTION_NAME,
                    payload=p,
                    points=[m.tmdb_id],
                    wait=False,
                )
            written += 1
        print(f"  {min(i + BATCH, len(movies)):>6}/{len(movies)}")

    print(f"\n{'Se escribirían' if args.dry_run else 'Escritos'} {written} puntos")
    for k, n in stats.items():
        print(f"   {k:18s} con valor: {n:>6} ({100 * n / len(movies):.1f}%)")
    if not args.dry_run:
        print("\nVerifica con: python scripts/verify_search_branches.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
