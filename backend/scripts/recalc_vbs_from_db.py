"""Recalculate VectorBox scores for every movie in the catalog using only DB data.

Unlike `recalc_scores.py` (which re-hits OMDb per movie and skips entries without
imdb_id), this script reuses the imdb_rating, metacritic_rating, vote_average,
imdb_vote_count and vote_count already stored in `movies`. Run this after
changing the VBS formula to backfill the new scores in seconds.

Usage:
    docker compose exec backend python scripts/recalc_vbs_from_db.py
"""
import asyncio
import logging
import os
import sys

sys.path.append(os.getcwd())

from sqlalchemy import select
from config import AsyncSessionLocal
from models.database import Movie
from models.external_schemas import OMDbResponse
from services.omdb_client import OMDbClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("recalc_vbs_from_db")


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


if __name__ == "__main__":
    asyncio.run(recalc())
