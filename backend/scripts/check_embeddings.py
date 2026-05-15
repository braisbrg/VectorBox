"""
Embedding sanity check — compares stored Qdrant vectors against a simple MiniLM
reference embedding generated from movie metadata. Low similarity scores indicate
potentially corrupt or mismatched embeddings.

Usage:
    docker compose exec backend python scripts/check_embeddings.py --limit 500
    docker compose exec backend python scripts/check_embeddings.py --threshold 0.3 --fix
    docker compose exec backend python scripts/check_embeddings.py --user-id 212
    docker compose exec backend python scripts/check_embeddings.py --limit 200 --update-db
"""
import argparse
import asyncio
import logging
import os
import sys

# Fix paths for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

import numpy as np
from sqlalchemy import select

from config import AsyncSessionLocal
from models.database import Movie, UserRating
from services.embedding_service import EmbeddingService
from services.qdrant_service import QdrantService

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("check_embeddings")


def _build_reference_text(movie: Movie) -> str:
    """Reference text for the embedding-quality cosine check.

    Mirrors `reembed_catalog._build_text`'s fallback recipe — overview +
    genres + keywords (+ directors + cast) — and INTENTIONALLY EXCLUDES
    title. Including the title biased the score against films with short
    or generic titles ('42', 'Z', 'Network'): the reference text became
    title-heavy and dominated the cosine, even though the stored vector
    (encoded from cinematic_description, 80 words of rich plot) was fine.
    Same no-title rule applies catalog-wide — see CLAUDE.md "Embedding &
    vector hygiene".

    The quality score now genuinely measures: "is the cinematic_description
    embedding in the same neighbourhood as the raw plot+metadata embedding
    of the same movie?" Low score → Groq hallucinated or enriched the
    wrong film.
    """
    parts: list[str] = []
    if movie.overview:
        parts.append(movie.overview)
    if movie.genres:
        parts.append("Genres: " + ", ".join(movie.genres))
    if movie.keywords:
        parts.append("Themes: " + ", ".join((movie.keywords or [])[:15]))
    if movie.directors:
        parts.append("Directors: " + ", ".join(movie.directors))
    if movie.cast:
        parts.append("Cast: " + ", ".join(movie.cast))
    return ". ".join(parts).strip()


async def check_movie_embedding(
    movie: Movie,
    qdrant: QdrantService,
    embedding_service: EmbeddingService,
    return_debug: bool = False,
):
    """
    Returns cosine similarity between stored Qdrant vector and a reference
    metadata embedding. Returns None if movie has no vector in Qdrant.

    When return_debug=True, returns a dict with keys:
      quality, reference_text, stored_vector, reference_vector
    (or None if check could not run).
    """
    stored_vector = await qdrant.get_vector(movie.tmdb_id)
    if not stored_vector:
        return None

    reference_text = _build_reference_text(movie)
    if not reference_text:
        return None

    loop = asyncio.get_running_loop()
    try:
        reference_vector = await loop.run_in_executor(
            None,
            lambda: embedding_service.generate_embedding(
                {"title": movie.title or ""}, text_override=reference_text
            ),
        )
    except Exception as e:
        logger.warning(f"Reference embedding failed for {movie.title}: {e}")
        return None

    if reference_vector is None:
        return None

    a = np.array(stored_vector)
    b = np.array(reference_vector)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return None
    quality = float(np.dot(a, b) / denom)
    if return_debug:
        return {
            "quality": quality,
            "reference_text": reference_text,
            "stored_vector": stored_vector,
            "reference_vector": reference_vector,
        }
    return quality


async def _re_enrich_movie(movie: Movie, llm_client, qdrant: QdrantService, embedding_service: EmbeddingService) -> bool:
    """Re-run cinematic enrichment for a single movie. Returns True on success."""
    from services.cinematic_enricher import generate_cinematic_description

    try:
        description, model_used = await generate_cinematic_description(
            title=movie.title or "",
            overview=movie.overview or "",
            genres=movie.genres or [],
            keywords=movie.keywords or [],
            directors=movie.directors or [],
            cast=movie.cast or [],
            year=movie.year or 0,
            groq_client=llm_client,
        )
    except Exception as e:
        logger.warning(f"Re-enrich LLM failed for {movie.title}: {e}")
        return False

    if not description or model_used is None:
        return False

    loop = asyncio.get_running_loop()
    try:
        vector = await loop.run_in_executor(
            None,
            lambda: embedding_service.generate_embedding(
                {"title": movie.title, "overview": movie.overview, "genres": movie.genres, "keywords": movie.keywords or []},
                text_override=description,
            ),
        )
    except Exception as e:
        logger.warning(f"Re-enrich encode failed for {movie.title}: {e}")
        return False

    if vector is None:
        return False

    payload = {
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "year": movie.year,
        "genres": movie.genres or [],
        "overview": movie.overview or "",
        "poster_path": movie.poster_path,
        "vote_average": movie.vote_average,
        "vote_count": movie.vote_count,
        "runtime": movie.runtime,
        "original_language": movie.original_language,
        "keywords": movie.keywords or [],
        "directors": movie.directors,
        "cast": movie.cast,
        "vectorbox_score": movie.vectorbox_score,
    }
    try:
        await qdrant.upsert_movie_vector(
            movie_id=movie.tmdb_id, vector=vector.tolist(), metadata=payload
        )
    except Exception as e:
        logger.warning(f"Re-enrich upsert failed for {movie.title}: {e}")
        return False

    movie.has_enriched_embedding = True
    movie.enriched_by_model = model_used
    movie.cinematic_description = description
    return True


async def main():
    parser = argparse.ArgumentParser(description="Embedding sanity check.")
    parser.add_argument("--limit", type=int, default=200, help="Max movies to check (default: 200)")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.25,
        help="Flag movies with similarity below this (default: 0.25)",
    )
    parser.add_argument("--fix", action="store_true", help="Re-enrich flagged movies (requires LLM API key)")
    parser.add_argument("--user-id", type=int, help="Check only movies in this user's ratings")
    parser.add_argument("--update-db", action="store_true", help="Write embedding_quality_score to DB")
    parser.add_argument("--recheck", action="store_true", help="Re-check movies that already have a quality score")
    parser.add_argument("--tmdb-id", type=int, help="Check a specific movie by TMDB ID")
    parser.add_argument("--verbose", action="store_true", help="Show reference text and vector sample")
    args = parser.parse_args()

    if args.tmdb_id is not None:
        args.limit = 1

    qdrant = QdrantService()
    embedding_service = EmbeddingService()

    llm_client = None
    if args.fix:
        from openai import AsyncOpenAI

        groq_key = os.getenv("GROQ_API_KEY")
        gemini_key = os.getenv("GEMINI_API_KEY")
        if groq_key:
            llm_client = AsyncOpenAI(
                api_key=groq_key,
                base_url="https://api.groq.com/openai/v1",
                max_retries=0,
            )
        elif gemini_key:
            llm_client = AsyncOpenAI(
                api_key=gemini_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
        else:
            print("--fix requires GROQ_API_KEY or GEMINI_API_KEY; aborting.")
            return

    flagged = 0
    ok = 0
    no_vector = 0
    fixed = 0

    try:
        async with AsyncSessionLocal() as db:
            if args.tmdb_id is not None:
                stmt = (
                    select(Movie)
                    .where(Movie.tmdb_id == args.tmdb_id)
                    .limit(1)
                )
            elif args.user_id is not None:
                stmt = (
                    select(Movie)
                    .join(UserRating, Movie.id == UserRating.movie_id)
                    .where(UserRating.user_id == args.user_id)
                    .distinct()
                    .limit(args.limit)
                )
            else:
                stmt = (
                    select(Movie)
                    .where(Movie.tmdb_id.isnot(None))
                    .order_by(Movie.id)
                    .limit(args.limit)
                )

            if not args.recheck and args.tmdb_id is None:
                stmt = stmt.where(Movie.embedding_quality_score.is_(None))

            result = await db.execute(stmt)
            movies = result.scalars().all()

            print(f"Checking {len(movies)} movies (threshold={args.threshold})...")

            for movie in movies:
                result_data = await check_movie_embedding(
                    movie, qdrant, embedding_service, return_debug=args.verbose
                )
                if result_data is None:
                    no_vector += 1
                    continue

                if args.verbose:
                    quality = result_data["quality"]
                else:
                    quality = result_data

                marker = "✓" if quality >= args.threshold else "⚠"
                flag_note = "  ← FLAGGED" if quality < args.threshold else ""
                title = (movie.title or "")[:35]
                print(f"{marker} {title:35s} quality={quality:.3f}{flag_note}")

                if args.verbose:
                    ref_text = result_data["reference_text"]
                    stored_vec = result_data["stored_vector"]
                    ref_vec = result_data["reference_vector"]
                    print(f"  Reference text: {ref_text[:100]}...")
                    print(f"  Stored vector[:5]:    {list(stored_vec)[:5]}")
                    print(f"  Reference vector[:5]: {list(ref_vec)[:5]}")
                    print(f"  Cosine similarity: {quality:.3f}")
                    if movie.cinematic_description:
                        print(f"  Cinematic desc: {movie.cinematic_description[:150]}...")

                if args.update_db:
                    movie.embedding_quality_score = quality

                if quality < args.threshold:
                    flagged += 1
                    if args.update_db:
                        movie.has_enriched_embedding = False
                    if args.fix and llm_client is not None:
                        if await _re_enrich_movie(movie, llm_client, qdrant, embedding_service):
                            fixed += 1
                else:
                    ok += 1

            if args.update_db:
                await db.commit()
                print("DB updated with embedding_quality_score values.")

    finally:
        if llm_client is not None:
            await llm_client.close()
        await qdrant.client.close()

    total = ok + flagged
    print(
        f"\nSummary: {ok}/{total} OK | {flagged} flagged (< {args.threshold}) | "
        f"{no_vector} without Qdrant vector"
        + (f" | {fixed} re-enriched" if args.fix else "")
    )


if __name__ == "__main__":
    asyncio.run(main())
