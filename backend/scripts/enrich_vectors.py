import asyncio
import os
import sys
import logging
import argparse
from sqlalchemy import select, func

# Fix paths
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AsyncSessionLocal
from models.database import Movie
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService
from tqdm import tqdm

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

async def enrich_vectors(missing_only: bool = True, limit: int = None):
    """
    Refetches keywords for movies and regenerates their vectors.
    """
    logger.info("Starting Keyword Enrichment & Vector Regeneration...")
    
    tmdb = TMDBClient()
    qdrant = QdrantService()
    embedding_service = EmbeddingService()
    
    # Init Qdrant just in case
    await qdrant.init_collection()

    async with AsyncSessionLocal() as db:
        # 1. Select Candidates
        query = select(Movie)
        
        if missing_only:
            logger.info("Targeting movies with EMPTY metadata (Keywords/Directors/Cast)...")
        else:
            logger.info("Targeting ALL movies (Force Refresh)...")

        result = await db.execute(query)
        all_movies = result.scalars().all()
        
        # Filter in memory
        candidates = []
        for m in all_movies:
            if missing_only:
                # Check keywords, directors, OR cast
                if m.keywords is None or m.directors is None or m.cast is None:
                    candidates.append(m)
            else:
                candidates.append(m)
        
        if limit:
            candidates = candidates[:limit]

        logger.info(f"files to process: {len(candidates)}")

        if not candidates:
            return

        # 2. Process
        pbar = tqdm(total=len(candidates), desc="Enriching Metadata")
        
        success_count = 0
        
        for movie in candidates:
            try:
                # Flag to check if we need to update DB
                db_updated = False
                
                # Check what is missing
                needs_keywords = movie.keywords is None or not missing_only
                needs_credits = movie.directors is None or movie.cast is None or not missing_only
                
                fetched_details = None
                
                # Fetch fresh details if needed
                if needs_keywords or needs_credits:
                    fetched_details = await tmdb.get_movie_details(movie.tmdb_id)
                    
                if fetched_details:
                    # Update Keywords
                    if needs_keywords:
                        movie.keywords = fetched_details.get("keywords_flat", [])
                        db_updated = True
                        
                    # Update Credits (Directors & Cast)
                    if needs_credits:
                        # Directors handled in get_movie_details -> "directors" key
                        movie.directors = fetched_details.get("directors", [])
                        
                        # Cast - Extract Top 3
                        cast_list = []
                        if "credits" in fetched_details and "cast" in fetched_details["credits"]:
                            # Sort by order just in case, though TMDB usually returns sorted
                            sorted_cast = sorted(fetched_details["credits"]["cast"], key=lambda x: x.get("order", 999))
                            cast_list = [member["name"] for member in sorted_cast[:3]]
                        
                        movie.cast = cast_list
                        db_updated = True

                    # Update Spanish Metadata (Self-Healing)
                    if not movie.title_es or not movie.overview_es:
                         if fetched_details.get("title_es"): 
                             movie.title_es = fetched_details.get("title_es")
                             db_updated = True
                         if fetched_details.get("overview_es"): 
                             movie.overview_es = fetched_details.get("overview_es")
                             db_updated = True
                
                if db_updated:
                    db.add(movie)

                # B. Generate NEW Embedding
                # We need genres as well
                genres = movie.genres or []
                overview = movie.overview or ""
                title = movie.title or ""
                keywords = movie.keywords or []
                
                # Optional: Include Directors/Cast in embedding text?
                # For now, sticking to standard v1 embedding logic to maintain consistency
                
                embedding_data = {
                    "title": title,
                    "overview": overview,
                    "genres": genres,
                    "keywords": keywords 
                }
                
                vector = embedding_service.generate_embedding(embedding_data)
                
                # C. Upsert to Qdrant
                payload = {
                    "tmdb_id": movie.tmdb_id,
                    "title": title,
                    "year": movie.year,
                    "genres": genres,
                    "overview": overview,
                    "poster_path": movie.poster_path,
                    "vote_average": movie.vote_average,
                    "vote_count": movie.vote_count,
                    "runtime": movie.runtime,
                    "original_language": movie.original_language,
                    "keywords": keywords,
                    "directors": movie.directors, # Add to payload
                    "cast": movie.cast,           # Add to payload
                    "vectorbox_score": movie.vectorbox_score,
                    "imdb_rating": movie.imdb_rating,
                    "metacritic_rating": movie.metacritic_rating,
                    "rotten_tomatoes_rating": movie.rotten_tomatoes_rating,
                    "title_es": movie.title_es,
                    "overview_es": movie.overview_es
                }
                
                await qdrant.upsert_movie_vector(
                    movie_id=movie.tmdb_id, # Use TMDB ID for consistency with seed_db and ingest
                    vector=vector.tolist(),
                    metadata=payload
                )
                
                success_count += 1
                
                # Commit every 50
                if success_count % 50 == 0:
                    await db.commit()
                    
            except Exception as e:
                logger.error(f"Failed to enrich movie {movie.title} ({movie.id}): {e}")
            
            pbar.update(1)
            
        await db.commit() # Final commit
        pbar.close()
        
    await tmdb.aclose()
    logger.info(f"Enrichment Complete. Updated {success_count} movies.")


MODEL_ALIASES = {
    "scout": "meta-llama/llama-4-scout-17b-16e-instruct",
    "70b":   "llama-3.3-70b-versatile",
    "8b":    "llama-3.1-8b-instant",
}


async def enrich_embeddings_via_groq(limit: int = None, model_only: str = None):
    """
    Re-processes movies where has_enriched_embedding is False.
    Generates cinematic descriptions via Groq and re-upserts vectors.
    """
    import os
    import sys
    from openai import AsyncOpenAI
    from services.cinematic_enricher import generate_cinematic_description, DailyLimitExhausted

    logger.info("Starting LLM-Enriched Embedding Generation via Groq...")

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.error("GROQ_API_KEY not set. Aborting.")
        return

    # Create Groq client ONCE at script start — max_retries=0 to prevent
    # OpenAI SDK internal 429 retry sleeps (our fallback chain handles retries)
    groq_client = AsyncOpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key, max_retries=0)
    qdrant = QdrantService()
    embedding_service = EmbeddingService()

    await qdrant.init_collection()

    # Counters for final summary
    total_processed = 0
    successful_enrichments = 0
    fallback_count = 0
    qdrant_errors = 0

    # Per-model tracking
    model_counts: dict[str, int] = {}
    model_samples: dict[str, tuple[str, str]] = {}  # model_id → (movie_title, description_preview)

    async with AsyncSessionLocal() as db:
        query = select(Movie).where(Movie.has_enriched_embedding.is_(False))
        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        candidates = result.scalars().all()

        logger.info(f"Found {len(candidates)} movies to enrich")

        if not candidates:
            print("\n=== Enrichment Summary ===")
            print("No movies need enrichment.")
            return

        # Process in batches of 10
        batch_size = 10

        for batch_start in range(0, len(candidates), batch_size):
            batch = candidates[batch_start:batch_start + batch_size]

            for movie in batch:
                total_processed += 1
                try:
                    # Generate cinematic description — returns (description, model_id)
                    description, model_used = await generate_cinematic_description(
                        title=movie.title or "",
                        overview=movie.overview or "",
                        genres=movie.genres or [],
                        keywords=movie.keywords or [],
                        directors=movie.directors or [],
                        cast=movie.cast or [],
                        year=movie.year or 0,
                        groq_client=groq_client,
                        force_model=model_only,
                    )

                    # Generate embedding
                    vector = embedding_service.generate_embedding(
                        {"title": movie.title, "overview": movie.overview, "genres": movie.genres, "keywords": movie.keywords or []},
                        text_override=description,
                    )

                    # Upsert to Qdrant
                    try:
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
                            "imdb_rating": movie.imdb_rating,
                            "metacritic_rating": movie.metacritic_rating,
                            "title_es": movie.title_es,
                            "overview_es": movie.overview_es,
                        }

                        await qdrant.upsert_movie_vector(
                            movie_id=movie.tmdb_id,
                            vector=vector.tolist(),
                            metadata=payload
                        )
                    except Exception as e:
                        logger.error(f"Qdrant upsert failed for {movie.title}: {e}")
                        qdrant_errors += 1
                        continue

                    # Track results
                    if model_used is not None:
                        movie.has_enriched_embedding = True
                        movie.enriched_by_model = model_used
                        successful_enrichments += 1
                        model_counts[model_used] = model_counts.get(model_used, 0) + 1
                        # Store first sample per model for quality comparison
                        if model_used not in model_samples:
                            preview = description[:200] + "..." if len(description) > 200 else description
                            model_samples[model_used] = (movie.title, preview)
                    else:
                        fallback_count += 1

                    db.add(movie)

                except DailyLimitExhausted as e:
                    # Graceful stop, save batch so far, and print summary
                    # Commit what we have before exiting early
                    await db.commit()
                    
                    # Compute remaining movies
                    count_q = select(func.count()).select_from(Movie).where(Movie.has_enriched_embedding.is_(False))
                    remaining_count = await db.scalar(count_q)
                    
                    print(f"\n{str(e)}. Run again tomorrow to continue.")
                    print(f"{successful_enrichments} movies enriched today. {remaining_count} movies remaining (has_enriched_embedding=False).\n")
                    
                    # Print summary and exit
                    _print_enrichment_summary(total_processed, successful_enrichments, fallback_count, qdrant_errors, model_counts, model_samples)
                    sys.exit(0)

                except Exception as e:
                    logger.error(f"Failed to enrich {movie.title} ({movie.tmdb_id}): {e}")
                    fallback_count += 1

            # Commit after each batch
            await db.commit()

            # Rate limit: 2s between batches to stay within ~30 req/min
            if batch_start + batch_size < len(candidates):
                await asyncio.sleep(2.0)

            logger.info(f"Batch {batch_start // batch_size + 1} complete ({min(batch_start + batch_size, len(candidates))}/{len(candidates)})")

    # Final summary
    _print_enrichment_summary(total_processed, successful_enrichments, fallback_count, qdrant_errors, model_counts, model_samples)


def _print_enrichment_summary(total_processed, successful_enrichments, fallback_count, qdrant_errors, model_counts, model_samples):
    print("\n" + "=" * 60)
    print("  LLM EMBEDDING ENRICHMENT SUMMARY")
    print("=" * 60)
    print(f"  Total movies processed:     {total_processed}")
    print(f"  Successful enrichments:     {successful_enrichments}")
    print(f"  Fallbacks (all models failed): {fallback_count}")
    print(f"  Qdrant upsert errors:       {qdrant_errors}")
    print(f"  Success rate:               {successful_enrichments}/{total_processed} ({(successful_enrichments/max(total_processed,1)*100):.1f}%)")
    print()
    print("  Breakdown by model:")
    for model_id, count in model_counts.items():
        short_name = model_id.split("/")[-1]
        print(f"    {short_name:40s} {count:>5d} movies")
    print()
    if model_samples:
        print("  Sample descriptions (one per model):")
        print("-" * 60)
        for model_id, (title, preview) in model_samples.items():
            short_name = model_id.split("/")[-1]
            print(f"  [{short_name}] {title}:")
            print(f"    {preview}")
            print()
    print("=" * 60 + "\n")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Process ALL movies, not just those missing keywords")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of movies processed")
    parser.add_argument("--enrich-embeddings", action="store_true", help="Re-process movies without LLM-enriched embeddings via Groq")
    parser.add_argument(
        "--model-only",
        type=str,
        default=None,
        help="Restrict enrichment to a single Groq model. No fallback to other models. "
             "Stops gracefully when the daily limit for that model is exhausted. "
             "Example: --model-only scout"
    )
    parser.add_argument(
        "--reset-enrichment",
        action="store_true",
        help="Reset has_enriched_embedding=False and enriched_by_model=None for ALL movies. "
             "Use before re-running with a higher quality model."
    )
    args = parser.parse_args()
    
    if args.reset_enrichment:
        async def run_reset():
            async with AsyncSessionLocal() as db:
                count_q = select(func.count()).select_from(Movie).where(Movie.has_enriched_embedding.is_(True))
                total_enriched = await db.scalar(count_q)
                print(f"Found {total_enriched} movies currently enriched.")
                print("Resetting has_enriched_embedding=False and enriched_by_model=None for ALL movies...")
                from sqlalchemy import update
                stmt = update(Movie).values(has_enriched_embedding=False, enriched_by_model=None)
                await db.execute(stmt)
                await db.commit()
                print("Reset complete. Do not forget to re-run the enrichment.")
        asyncio.run(run_reset())
        sys.exit(0)
    
    model_only_id = None
    if args.model_only:
        if args.model_only not in MODEL_ALIASES:
            print(f"Error: Invalid model alias '{args.model_only}'.")
            print(f"Valid options: {', '.join(MODEL_ALIASES.keys())}")
            sys.exit(1)
        model_only_id = MODEL_ALIASES[args.model_only]
    
    if args.enrich_embeddings:
        asyncio.run(enrich_embeddings_via_groq(limit=args.limit, model_only=model_only_id))
    else:
        asyncio.run(enrich_vectors(missing_only=not args.all, limit=args.limit))
