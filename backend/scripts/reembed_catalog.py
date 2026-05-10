"""Re-embed every movie in the catalog without title (Variant A fix).

Drops title from the encoded text to eliminate token-overlap leakage that
makes BYW/Magic Box pull off-theme neighbours (Howl's → The Howling, Faster
Faster → Fast X). Same model (all-MiniLM-L6-v2), same dimension (384), so
the Qdrant collection stays in place — we just upsert new vectors.

When `cinematic_description` is present (Groq-enriched, ~99% of catalog), it
is used as the embedding text directly — captures deeper themes than the raw
TMDB overview (e.g. La Chimera's "dreamlike melancholic exploration of
identity and morality" vs. TMDB's plot summary about treasure hunting).

Usage:
    docker compose exec backend python scripts/reembed_catalog.py
"""
import asyncio
import logging
import os
import sys

sys.path.append(os.getcwd())

import numpy as np
from sqlalchemy import select
from config import AsyncSessionLocal
from models.database import Movie
from services.embedding_service import get_model
from services.qdrant_service import QdrantService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("reembed")

BATCH = 256


def _qdrant_payload(m: Movie) -> dict:
    return {
        "tmdb_id": m.tmdb_id,
        "title": m.title,
        "year": m.year,
        "genres": m.genres or [],
        "overview": m.overview or "",
        "poster_path": m.poster_path,
        "vote_average": m.vote_average,
        "vote_count": m.vote_count,
        "runtime": m.runtime,
        "original_language": m.original_language,
        "keywords": m.keywords or [],
        "directors": m.directors,
        "cast": m.cast,
        "vectorbox_score": m.vectorbox_score,
        "imdb_rating": m.imdb_rating,
        "metacritic_rating": m.metacritic_rating,
        "title_es": m.title_es,
        "overview_es": m.overview_es,
    }


def _build_text(m: Movie) -> str | None:
    """Pick the richest text available for embedding.
    Priority: cinematic_description (Groq-enriched) → overview+genres+keywords."""
    if m.cinematic_description and m.cinematic_description.strip():
        return m.cinematic_description.strip()
    parts = []
    if m.overview:
        parts.append(m.overview)
    if m.genres:
        parts.append(f"Genres: {', '.join(m.genres)}")
    if m.keywords:
        parts.append(f"Themes: {', '.join((m.keywords or [])[:15])}")
    text = ". ".join(parts).strip()
    return text or None


async def reembed():
    model = get_model()
    qdrant = QdrantService()

    async with AsyncSessionLocal() as db:
        movies = (await db.execute(select(Movie).order_by(Movie.id))).scalars().all()
        total = len(movies)
        logger.info(f"Re-embedding {total} movies (cinematic_description preferred, no title)")

        skipped = 0
        upserted = 0
        used_cinematic = 0
        used_fallback = 0

        for i in range(0, total, BATCH):
            chunk = movies[i : i + BATCH]
            texts = []
            chunk_movies = []
            for m in chunk:
                t = _build_text(m)
                if not t:
                    skipped += 1
                    continue
                texts.append(t)
                chunk_movies.append(m)
                if m.cinematic_description and m.cinematic_description.strip():
                    used_cinematic += 1
                else:
                    used_fallback += 1

            if not texts:
                continue

            vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)

            for m, v in zip(chunk_movies, vectors):
                await qdrant.upsert_movie_vector(
                    movie_id=m.tmdb_id,
                    vector=v.tolist(),
                    metadata=_qdrant_payload(m),
                )
                upserted += 1

            logger.info(
                f"  {min(i + BATCH, total)}/{total} processed "
                f"(upserted={upserted}, skipped={skipped}, "
                f"cinematic={used_cinematic}, fallback={used_fallback})"
            )

    logger.info("=" * 60)
    logger.info(
        f"Done. Upserted={upserted}, skipped={skipped} (no text), total={total}, "
        f"cinematic_description used={used_cinematic}, fallback used={used_fallback}"
    )


if __name__ == "__main__":
    asyncio.run(reembed())
