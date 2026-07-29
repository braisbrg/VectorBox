"""Purge hallucinated enrichment.

Films that were LLM-enriched despite having no real source text (empty /
near-empty overview) got a FABRICATED cinematic description → a garbage
embedding that scores high against unrelated films (the "Wolf Totem" / tmdb
417613 phantom that landed #2 nearest to Nausicaä).

This flips has_enriched_embedding=False in BOTH Postgres and the Qdrant payload
(so a later `recalc_vbs_from_db --sync-payload-only` can't re-enable them) and
clears the hallucinated `cinematic_description` / `enriched_by_model`. The film
keeps its Qdrant point (still reachable via direct lookup / autocomplete) but is
invisible to gated recommendations.

Root cause is fixed forward in `cinematic_enricher` (refuses <20-char
overviews); this is the one-off backfill for rows enriched before that guard.

Usage:
    python scripts/purge_hallucinated_enrichment.py --dry-run
    python scripts/purge_hallucinated_enrichment.py
"""
import argparse
import asyncio
import logging
import os
import sys

from sqlalchemy import select, func, and_

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AsyncSessionLocal
from models.database import Movie
from services.qdrant_service import QdrantService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Must match the enricher's refusal threshold (cinematic_enricher.py).
MIN_OVERVIEW_CHARS = 20


async def main(dry_run: bool) -> None:
    async with AsyncSessionLocal() as db:
        stmt = select(Movie).where(and_(
            Movie.has_enriched_embedding.is_(True),
            func.length(func.trim(func.coalesce(Movie.overview, ""))) < MIN_OVERVIEW_CHARS,
        ))
        movies = (await db.execute(stmt)).scalars().all()
        tmdb_ids = [m.tmdb_id for m in movies]

        print(f"Found {len(movies)} enriched films with <{MIN_OVERVIEW_CHARS}-char overview.")
        for m in movies[:20]:
            print(f"  tmdb={m.tmdb_id} year={m.year} '{(m.title or '')[:40]}'")
        if len(movies) > 20:
            print(f"  … and {len(movies) - 20} more")

        if not movies:
            return
        if dry_run:
            print("DRY RUN — no changes written.")
            return

        for m in movies:
            m.has_enriched_embedding = False
            m.enriched_by_model = None
            m.cinematic_description = None
        await db.commit()
        print(f"Postgres: cleared enrichment on {len(movies)} rows.")

    q = QdrantService()
    try:
        # Batched payload flip; keeps the point (autocomplete) but gates it out.
        for i in range(0, len(tmdb_ids), 500):
            chunk = tmdb_ids[i:i + 500]
            await q.client.set_payload(
                collection_name=q.COLLECTION_NAME,
                payload={"has_enriched_embedding": False},
                points=chunk,
            )
        print(f"Qdrant: flipped has_enriched_embedding=False on {len(tmdb_ids)} points.")
    finally:
        await q.client.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.dry_run))
