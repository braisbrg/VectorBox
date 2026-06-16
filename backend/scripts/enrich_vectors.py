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
    "gemini":      "gemini-2.5-flash",
    "scout":       "meta-llama/llama-4-scout-17b-16e-instruct",
    "70b":         "llama-3.3-70b-versatile",
    "8b":          "llama-3.1-8b-instant",
    "oss-120":     "openai/gpt-oss-120b",
    "oss-20":      "openai/gpt-oss-20b",
    "qwen3-32b":   "qwen/qwen3-32b",
    "qwen3.6-27b": "qwen/qwen3.6-27b",
}

# When a restricted chain returns the legacy fallback (model_used=None) this
# many times in a row, the day's quota for every model in the chain is almost
# certainly exhausted — stop gracefully instead of churning thousands of films.
STOP_AFTER_CONSECUTIVE_FALLBACKS = 8


async def enrich_embeddings_via_groq(
    limit: int = None,
    model_only: str = None,
    model_chain_override: list[str] | None = None,
):
    """
    Re-processes movies where has_enriched_embedding is False.
    Generates cinematic descriptions via LLM (Gemini preferred, Groq fallback) and re-upserts vectors.

    Args:
        model_only: Force a single model (mutually exclusive with model_chain_override).
        model_chain_override: Restrict the fallback chain to a custom list of model IDs
            (e.g. the top-4 for --smart). Forwarded to generate_cinematic_description.
    """
    import sys
    from openai import AsyncOpenAI
    from services.cinematic_enricher import generate_cinematic_description, DailyLimitExhausted

    gemini_key = os.environ.get("GEMINI_API_KEY")
    groq_key = os.environ.get("GROQ_API_KEY")

    if groq_key:
        logger.info("Starting LLM-Enriched Embedding Generation via Groq...")
        # max_retries=0: our fallback chain handles retries, not the SDK
        groq_client = AsyncOpenAI(
            api_key=groq_key,
            base_url="https://api.groq.com/openai/v1",
            max_retries=0,
            timeout=40.0,  # REL-4: bound calls (SDK default 600s) so a hung
                           # request can't stall an unattended bulk run
        )
        # Groq ~30 RPM free tier — conservative pacing
        batch_size = 10
        batch_delay = 2.0
    elif gemini_key:
        logger.info("Starting LLM-Enriched Embedding Generation via Gemini Flash 2.5 (Groq not configured)...")
        groq_client = AsyncOpenAI(
            api_key=gemini_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        # Gemini paid tier: 1000 RPM — use larger batches and shorter delays
        batch_size = 30
        batch_delay = 0.3
    else:
        logger.error("Neither GEMINI_API_KEY nor GROQ_API_KEY is set. Aborting.")
        return
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

        consecutive_fallbacks = 0
        for batch_start in range(0, len(candidates), batch_size):
            batch = candidates[batch_start:batch_start + batch_size]

            for movie in batch:
                total_processed += 1
                try:
                    async def _generate():
                        return await generate_cinematic_description(
                            title=movie.title or "",
                            overview=movie.overview or "",
                            genres=movie.genres or [],
                            keywords=movie.keywords or [],
                            directors=movie.directors or [],
                            cast=movie.cast or [],
                            year=movie.year or 0,
                            groq_client=groq_client,
                            force_model=model_only,
                            model_chain_override=model_chain_override,
                        )

                    # Generate cinematic description — returns (description, model_id).
                    description, model_used = await _generate()

                    # model_used is None == every model in the chain returned the
                    # crude legacy concatenation. That happens for two very
                    # different reasons: a transient network blip (it hits ALL
                    # models at once) vs. real daily-quota exhaustion. Retry once
                    # after a backoff to tell them apart — a blip clears, real
                    # exhaustion persists — so a momentary hiccup doesn't truncate
                    # the session (and waste the remaining daily quota).
                    if model_used is None:
                        await asyncio.sleep(10)
                        description, model_used = await _generate()

                    # Still None == genuinely exhausted/failing. DON'T re-embed or
                    # upsert the legacy text — it would overwrite a good existing
                    # vector. Leave has_enriched_embedding=False so the film is
                    # retried next session; stop the run if this keeps happening.
                    if model_used is None:
                        fallback_count += 1
                        consecutive_fallbacks += 1
                        if consecutive_fallbacks >= STOP_AFTER_CONSECUTIVE_FALLBACKS:
                            await db.commit()
                            remaining = await db.scalar(
                                select(func.count()).select_from(Movie).where(Movie.has_enriched_embedding.is_(False))
                            )
                            print(f"\n{consecutive_fallbacks} consecutive fallbacks — chain quota exhausted. "
                                  f"{successful_enrichments} enriched this session, {remaining} remaining. "
                                  f"Run again to continue.\n")
                            _print_enrichment_summary(total_processed, successful_enrichments, fallback_count, qdrant_errors, model_counts, model_samples)
                            return
                        continue
                    consecutive_fallbacks = 0

                    # Generate embedding from the LLM description
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
                    movie.has_enriched_embedding = True
                    movie.enriched_by_model = model_used
                    movie.cinematic_description = description
                    # Invalidate the stale quality score (it was computed against
                    # the OLD vector). NULL == "needs audit": maintenance Phase 2
                    # recomputes it against the new name-free reference. NULL is
                    # treated as "allow through" by every runtime gate, which is
                    # correct for a freshly-enriched film.
                    movie.embedding_quality_score = None
                    successful_enrichments += 1
                    model_counts[model_used] = model_counts.get(model_used, 0) + 1
                    # Store first sample per model for quality comparison
                    if model_used not in model_samples:
                        preview = description[:200] + "..." if len(description) > 200 else description
                        model_samples[model_used] = (movie.title, preview)

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

            # Pacing: Gemini=0.5s (1000 RPM), Groq=2.0s (~30 RPM free tier)
            if batch_start + batch_size < len(candidates):
                await asyncio.sleep(batch_delay)

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


async def enrich_embeddings_parallel(models: list[str], limit: int = None):
    """Run ONE worker per model CONCURRENTLY, each pinned to its own model.

    Why this is faster than the sequential --chain: every model has an
    INDEPENDENT Groq rate-limit bucket (separate TPM/RPM/RPD). The sequential
    chain tries the first model for every film, so the whole run bottlenecks on
    that one model's TPM (qwen3-32b = 6K/min) while the second model's bucket
    sits idle until the first model's DAILY limit is hit. Pinning one worker per
    model lets the second enrich films during the first's per-minute cooldown —
    combined TPM ≈ the sum, so the shared daily quota (still ~1K films/model)
    drains in roughly half the wall-clock. Total/day is unchanged (RPD-bound);
    each session just finishes ~2× sooner.

    Films are pulled from a shared asyncio.Queue (get_nowait is atomic under the
    single-threaded event loop, so no two workers grab the same film). Each
    worker owns its own AsyncSession (never shared across tasks). Embedding
    encode is serialised behind a lock — it's fast (~0.15s) and the model is a
    shared singleton; the slow, rate-limited LLM calls are what run in parallel.
    """
    from openai import AsyncOpenAI
    from services.cinematic_enricher import generate_cinematic_description, DailyLimitExhausted
    from scripts.reembed_catalog import _qdrant_payload

    if not os.environ.get("GROQ_API_KEY"):
        logger.error("GROQ_API_KEY not set. Aborting.")
        return

    client = AsyncOpenAI(
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1",
        max_retries=0,
        timeout=40.0,  # REL-4
    )
    qdrant = QdrantService()
    embedding_service = EmbeddingService()
    await qdrant.init_collection()
    embed_lock = asyncio.Lock()

    async with AsyncSessionLocal() as db:
        q = select(Movie.id).where(Movie.has_enriched_embedding.is_(False))
        if limit:
            q = q.limit(limit)
        ids = list((await db.execute(q)).scalars().all())

    logger.info(f"Parallel enrich: {len(ids)} films across {len(models)} concurrent workers {models}")
    if not ids:
        print("No movies need enrichment.")
        return

    queue: asyncio.Queue = asyncio.Queue()
    for mid in ids:
        queue.put_nowait(mid)

    stats = {"success": 0, "fallback": 0, "qdrant_err": 0, "by_model": {}}

    async def worker(model_id: str):
        done = 0
        async with AsyncSessionLocal() as db:
            while True:
                try:
                    movie_id = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                # Wrap the WHOLE per-film body: a DB blip, network hiccup, or
                # qdrant error must never kill the worker (which would propagate
                # through gather and tear down the sibling worker too — that's
                # how a session ends with quota to spare). DailyLimitExhausted is
                # the only clean stop. Everything else → rollback + continue.
                try:
                    movie = await db.get(Movie, movie_id)
                    if movie is None or movie.has_enriched_embedding:
                        continue
                    try:
                        desc, used = await generate_cinematic_description(
                            title=movie.title or "", overview=movie.overview or "",
                            genres=movie.genres or [], keywords=movie.keywords or [],
                            directors=movie.directors or [], cast=movie.cast or [],
                            year=movie.year or 0, groq_client=client, force_model=model_id,
                        )
                    except DailyLimitExhausted:
                        queue.put_nowait(movie_id)
                        try:
                            await db.commit()
                        except Exception:
                            await db.rollback()
                        logger.info(f"[{model_id}] daily limit reached — worker stopping ({done} done)")
                        return

                    if not desc or used is None:
                        stats["fallback"] += 1
                        await asyncio.sleep(2)
                        continue

                    loop = asyncio.get_running_loop()
                    async with embed_lock:
                        vector = await loop.run_in_executor(
                            None,
                            lambda: embedding_service.generate_embedding(
                                {"title": movie.title, "overview": movie.overview,
                                 "genres": movie.genres, "keywords": movie.keywords or []},
                                text_override=desc,
                            ),
                        )
                    await qdrant.upsert_movie_vector(
                        movie_id=movie.tmdb_id, vector=vector.tolist(), metadata=_qdrant_payload(movie)
                    )

                    movie.has_enriched_embedding = True
                    movie.enriched_by_model = used
                    movie.cinematic_description = desc
                    movie.embedding_quality_score = None  # invalidate stale; Phase 2 re-audits
                    stats["success"] += 1
                    stats["by_model"][used] = stats["by_model"].get(used, 0) + 1
                    done += 1
                    if done % 10 == 0:
                        await db.commit()
                except Exception as e:
                    logger.warning(f"[{model_id}] transient error on {movie_id}: {e}")
                    try:
                        await db.rollback()
                    except Exception:
                        pass
                    await asyncio.sleep(5)
                    continue
            try:
                await db.commit()
            except Exception:
                await db.rollback()

    import time
    started = time.time()
    # return_exceptions=True: a fully-crashed worker (should be impossible now)
    # still can't tear down its sibling — the other keeps draining.
    results = await asyncio.gather(*[worker(m) for m in models], return_exceptions=True)
    for m, r in zip(models, results):
        if isinstance(r, Exception):
            logger.error(f"[{m}] worker died: {r}")
    elapsed = time.time() - started

    async with AsyncSessionLocal() as db:
        remaining = await db.scalar(
            select(func.count()).select_from(Movie).where(Movie.has_enriched_embedding.is_(False))
        )
    print(f"\nParallel session done in {elapsed:.0f}s. {stats['success']} enriched "
          f"({stats['by_model']}), {stats['fallback']} fallbacks, {stats['qdrant_err']} qdrant errors. "
          f"{remaining} remaining. Run again to continue.\n")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Process ALL movies, not just those missing keywords")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of movies processed")
    parser.add_argument("--enrich-embeddings", action="store_true", help="Re-process movies without LLM-enriched embeddings (Gemini preferred, Groq fallback)")
    parser.add_argument(
        "--model-only",
        type=str,
        default=None,
        help="Restrict enrichment to a single model alias. No fallback to other models. "
             "Stops gracefully when the daily limit for that model is exhausted. "
             "Aliases: gemini | scout | 70b | 8b | oss-120 | oss-20. "
             "Example: --model-only 70b  OR  --model-only oss-120"
    )
    parser.add_argument(
        "--smart",
        action="store_true",
        help="Restrict enrichment to the top-4 models (70B, Scout, oss-120, oss-20) — "
             "skips the weaker 8b-instant fallback. Use this when you want consistent "
             "high-quality cinematic descriptions across the whole catalogue. "
             "Mutually exclusive with --model-only."
    )
    parser.add_argument(
        "--chain",
        type=str,
        default=None,
        help="Restrict enrichment to a custom, ordered chain of model aliases "
             "(comma-separated). The 2026-06 sweep found qwen3-32b + oss-120 the "
             "best free pair for V2 descriptions. Example: --chain qwen3-32b,oss-120. "
             "Mutually exclusive with --model-only and --smart."
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run the --chain models CONCURRENTLY (one worker per model) instead "
             "of sequential fallback. Each model has its own rate-limit bucket, so "
             "this drains the shared daily quota in ~half the wall-clock. "
             "Requires --chain with 2+ models. Example: "
             "--chain qwen3-32b,oss-120 --parallel"
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
    
    if sum(bool(x) for x in (args.model_only, args.smart, args.chain)) > 1:
        print("Error: --model-only, --smart, and --chain are mutually exclusive.")
        sys.exit(1)

    model_only_id = None
    if args.model_only:
        if args.model_only not in MODEL_ALIASES:
            print(f"Error: Invalid model alias '{args.model_only}'.")
            print(f"Valid options: {', '.join(MODEL_ALIASES.keys())}")
            sys.exit(1)
        model_only_id = MODEL_ALIASES[args.model_only]

    if args.chain:
        chain_override = []
        for alias in (a.strip() for a in args.chain.split(",") if a.strip()):
            if alias not in MODEL_ALIASES:
                print(f"Error: Invalid model alias '{alias}' in --chain.")
                print(f"Valid options: {', '.join(MODEL_ALIASES.keys())}")
                sys.exit(1)
            chain_override.append(MODEL_ALIASES[alias])
    elif args.smart:
        # --smart: skip the 8B fallback to keep quality uniform across catalogue.
        chain_override = [
            "llama-3.3-70b-versatile",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
        ]
    else:
        chain_override = None

    if args.parallel:
        if not chain_override or len(chain_override) < 2:
            print("Error: --parallel requires --chain with 2+ models "
                  "(e.g. --chain qwen3-32b,oss-120 --parallel).")
            sys.exit(1)
        if not args.enrich_embeddings:
            print("Error: --parallel only applies to --enrich-embeddings.")
            sys.exit(1)
        asyncio.run(enrich_embeddings_parallel(models=chain_override, limit=args.limit))
    elif args.enrich_embeddings:
        asyncio.run(enrich_embeddings_via_groq(
            limit=args.limit,
            model_only=model_only_id,
            model_chain_override=chain_override,
        ))
    else:
        asyncio.run(enrich_vectors(missing_only=not args.all, limit=args.limit))
