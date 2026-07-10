"""Recalculate VectorBox scores for every movie in the catalog using only DB data.

Unlike `recalc_scores.py` (which re-hits OMDb per movie and skips entries without
imdb_id), this script reuses the imdb_rating, metacritic_rating, vote_average,
imdb_vote_count and vote_count already stored in `movies`. Run this after
changing the VBS formula to backfill the new scores in seconds.

AUD-DATA-3 (fixed 2026-07-10): the run now ALSO syncs the recalculated scores
into the Qdrant payload (`vectorbox_score`), which the rail Q-slider and the F8
filtered feed filter on INSIDE the vector search. Before this, payloads were
only stamped at (re-)embed time — a post-embed recalc left them stale (measured
39% drift, worst 25.6 points → films wrongly excluded from Q-filtered searches).

Usage:
    docker compose exec backend python scripts/recalc_vbs_from_db.py
    docker compose exec backend python scripts/recalc_vbs_from_db.py --sync-payload-only
        (no recalculation — just pushes the current PG scores into Qdrant)
"""
import asyncio
import logging
import os
import sys

sys.path.append(os.getcwd())

from qdrant_client import models as qmodels
from sqlalchemy import select
from config import AsyncSessionLocal
from models.database import Movie
from models.external_schemas import OMDbResponse
from services.omdb_client import OMDbClient
from services.qdrant_service import QdrantService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("recalc_vbs_from_db")


async def sync_payloads() -> None:
    """Push every movie's current PG `vectorbox_score` into its Qdrant payload.

    Batched (500 ops/request). Films with VBS=None get payload null so they
    can't ride a stale value past a Q filter. Missing points are skipped by
    Qdrant silently (vector may not exist yet — embed jobs stamp it on upsert).
    """
    qdrant = QdrantService()
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(Movie.tmdb_id, Movie.vectorbox_score))).all()
        logger.info(f"Syncing vectorbox_score payload for {len(rows)} films into Qdrant…")
        BATCH = 500
        for i in range(0, len(rows), BATCH):
            ops = [
                qmodels.SetPayloadOperation(
                    set_payload=qmodels.SetPayload(
                        payload={"vectorbox_score": score},
                        points=[tmdb_id],
                    )
                )
                for tmdb_id, score in rows[i : i + BATCH]
            ]
            await qdrant.client.batch_update_points(
                collection_name=qdrant.COLLECTION_NAME, update_operations=ops
            )
            logger.info(f"  payload sync {min(i + BATCH, len(rows))}/{len(rows)}")
        logger.info("Qdrant payload sync complete.")
    finally:
        await qdrant.aclose()


def _synthetic_omdb(movie: Movie) -> OMDbResponse:
    return OMDbResponse(
        Response="True",
        imdbRating=str(movie.imdb_rating) if movie.imdb_rating is not None else None,
        Metascore=str(movie.metacritic_rating) if movie.metacritic_rating is not None else None,
        imdbVotes=str(movie.imdb_vote_count) if movie.imdb_vote_count else None,
    )


async def recalc():
    omdb = OMDbClient.__new__(OMDbClient)  # bypass __init__ (no API calls)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Movie).order_by(Movie.id))
        movies = result.scalars().all()
        total = len(movies)
        logger.info(f"Recalculating VBS for {total} movies (DB-only, no API hits)…")

        updated = 0
        cleared = 0
        unchanged = 0
        delta_sum = 0.0

        for i, m in enumerate(movies, 1):
            previous = m.vectorbox_score
            vb = omdb.calculate_vectorbox_score(
                _synthetic_omdb(m),
                m.vote_average,
                tmdb_vote_count=m.vote_count,
                imdb_vote_count=m.imdb_vote_count,
            )

            if vb.score is None:
                # No usable data — clear out any stale VBS so it stops
                # appearing as a 98.0 ghost from the old formula.
                if previous is not None:
                    m.vectorbox_score = None
                    cleared += 1
                else:
                    unchanged += 1
                continue

            new_score = vb.score
            if previous is None or abs((previous or 0) - new_score) > 0.05:
                m.vectorbox_score = new_score
                if previous is not None:
                    delta_sum += new_score - previous
                updated += 1
            else:
                unchanged += 1

            if i % 500 == 0:
                await db.commit()
                logger.info(
                    f"  {i}/{total} processed — updated={updated} cleared={cleared} unchanged={unchanged}"
                )

        await db.commit()

    logger.info("=" * 60)
    logger.info(f"Done. Total={total}  Updated={updated}  Cleared={cleared}  Unchanged={unchanged}")
    if updated:
        logger.info(f"Average score delta on updated rows: {delta_sum / updated:+.2f}")

    # AUD-DATA-3: keep the Qdrant copy in lockstep with PG on every recalc.
    await sync_payloads()


if __name__ == "__main__":
    if "--sync-payload-only" in sys.argv:
        asyncio.run(sync_payloads())
    else:
        asyncio.run(recalc())
