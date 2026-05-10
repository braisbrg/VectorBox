"""
Maintenance Orchestrator — runs all DB maintenance phases respecting API budgets.

Phases (run in order, can be filtered with --phases):
    1. refresh_metadata    — OMDb refetch + recalc vectorbox_score with new formula.
                             Targets movies with NULL imdb_vote_count OR stale
                             last_metadata_refresh. Hits OMDb budget.
    2. embedding_audit     — populates embedding_quality_score for movies that
                             don't have one. Local compute only (no external API).
    3. embedding_repair    — re-enriches movies with low quality_score or
                             has_enriched_embedding=False. Hits Groq daily limits.
    4. backfill_descriptions — fills cinematic_description for already-enriched
                             movies that don't have it. Hits Groq.
    5. reset_profiles      — rebuilds user clusters. No external API.

OMDb budget is tracked in the `api_budget` table (1000/day default).
Groq budget is implicit: phases stop gracefully on DailyLimitExhausted.

Usage:
    docker compose exec backend python scripts/maintenance_orchestrator.py
    docker compose exec backend python scripts/maintenance_orchestrator.py --phases 1
    docker compose exec backend python scripts/maintenance_orchestrator.py --omdb-budget 500
    docker compose exec backend python scripts/maintenance_orchestrator.py --dry-run
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta
from typing import List, Optional

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

from sqlalchemy import select, func, or_, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import AsyncSessionLocal
from models.database import Movie, ApiBudget
from services.tmdb_client import TMDBClient
from services.omdb_client import OMDbClient
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService

# Reuse existing single-movie helpers
from scripts.refresh_metadata import refresh_movie, mark_released_upcoming
from scripts.check_embeddings import check_movie_embedding, _re_enrich_movie

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("maintenance")


# ---------------------------------------------------------------------------
# OMDb budget helpers (api_budget table)
# ---------------------------------------------------------------------------

DEFAULT_OMDB_DAILY_LIMIT = 1000


async def get_or_create_today_budget(db, override_limit: Optional[int] = None) -> ApiBudget:
    today = date.today()
    row = (await db.execute(select(ApiBudget).where(ApiBudget.date == today))).scalar_one_or_none()
    if row is None:
        row = ApiBudget(
            date=today,
            omdb_calls_used=0,
            omdb_calls_limit=override_limit or DEFAULT_OMDB_DAILY_LIMIT,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    elif override_limit is not None and row.omdb_calls_limit != override_limit:
        row.omdb_calls_limit = override_limit
        await db.commit()
    return row


async def increment_omdb_used(db, n: int) -> None:
    today = date.today()
    stmt = (
        pg_insert(ApiBudget)
        .values(date=today, omdb_calls_used=n, omdb_calls_limit=DEFAULT_OMDB_DAILY_LIMIT)
        .on_conflict_do_update(
            index_elements=["date"],
            set_={"omdb_calls_used": ApiBudget.omdb_calls_used + n},
        )
    )
    await db.execute(stmt)
    await db.commit()


# ---------------------------------------------------------------------------
# Phase 1 — refresh_metadata
# ---------------------------------------------------------------------------

async def phase_refresh_metadata(omdb_budget: int, dry_run: bool) -> dict:
    """
    Targets movies that either:
      - have imdb_id but no imdb_vote_count (F-16 backfill), OR
      - have stale last_metadata_refresh (> 30 days, regardless of age cohort).
    Cap by remaining OMDb budget for today.
    """
    stats = {"queued": 0, "refreshed": 0, "skipped_budget": 0, "failed": 0, "omdb_used": 0}

    async with AsyncSessionLocal() as db:
        budget = await get_or_create_today_budget(db)
        remaining = max(0, budget.omdb_calls_limit - budget.omdb_calls_used)
        budget_cap = min(omdb_budget, remaining)
        logger.info(f"[Phase 1] OMDb budget today: used={budget.omdb_calls_used}/{budget.omdb_calls_limit}, remaining={remaining}, will use up to {budget_cap}")

        if budget_cap == 0:
            logger.warning("[Phase 1] OMDb budget exhausted for today, skipping.")
            return stats

        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        query = (
            select(Movie)
            .where(Movie.imdb_id.isnot(None))
            .where(
                or_(
                    Movie.imdb_vote_count.is_(None),
                    Movie.last_metadata_refresh.is_(None),
                    Movie.last_metadata_refresh < thirty_days_ago,
                )
            )
            .order_by(
                # Prioritize: missing imdb_vote_count first, then most-recently-watched
                # Movie.imdb_vote_count.is_(None).desc(),  # SQLAlchemy can't sort on .is_()
                Movie.popularity.desc().nullslast(),
            )
            .limit(budget_cap)
        )
        movies = (await db.execute(query)).scalars().all()
        stats["queued"] = len(movies)
        logger.info(f"[Phase 1] Queued {len(movies)} movies for refresh")

        if dry_run:
            for m in movies[:10]:
                logger.info(f"  DRY-RUN would refresh: {m.id} {m.title} ({m.year})  imdb_votes={m.imdb_vote_count}")
            if len(movies) > 10:
                logger.info(f"  ... +{len(movies)-10} more")
            return stats

        tmdb = TMDBClient()
        omdb = OMDbClient()
        try:
            for movie in movies:
                ok = await refresh_movie(movie, tmdb, omdb)
                if ok:
                    stats["refreshed"] += 1
                else:
                    stats["failed"] += 1
                stats["omdb_used"] += 1  # refresh_movie always calls OMDb (when imdb_id set)

                # Persist progress every 25 movies (resilient to interruption)
                if stats["refreshed"] % 25 == 0 and stats["refreshed"] > 0:
                    await db.commit()

            await db.commit()
            await increment_omdb_used(db, stats["omdb_used"])
        finally:
            await tmdb.aclose()
            await omdb.close()

    logger.info(f"[Phase 1] Done: refreshed={stats['refreshed']}, failed={stats['failed']}, omdb_used={stats['omdb_used']}")
    return stats


# ---------------------------------------------------------------------------
# Phase 2 — embedding_audit (no external API)
# ---------------------------------------------------------------------------

async def phase_embedding_audit(limit: int, dry_run: bool) -> dict:
    """Populate embedding_quality_score for movies that have NULL."""
    stats = {"audited": 0, "low_quality": 0, "no_vector": 0}

    async with AsyncSessionLocal() as db:
        query = (
            select(Movie)
            .where(Movie.embedding_quality_score.is_(None))
            .where(Movie.has_enriched_embedding.is_(True))
            .order_by(Movie.popularity.desc().nullslast())
            .limit(limit)
        )
        movies = (await db.execute(query)).scalars().all()
        logger.info(f"[Phase 2] Queued {len(movies)} movies for embedding audit")

        if dry_run or not movies:
            return stats

        qdrant = QdrantService()
        embedding_service = EmbeddingService()
        try:
            for movie in movies:
                quality = await check_movie_embedding(movie, qdrant, embedding_service)
                if quality is None:
                    stats["no_vector"] += 1
                    continue
                movie.embedding_quality_score = quality
                if quality < 0.35:
                    stats["low_quality"] += 1
                stats["audited"] += 1

                if stats["audited"] % 50 == 0:
                    await db.commit()
            await db.commit()
        finally:
            pass  # services manage their own lifecycle

    logger.info(f"[Phase 2] Done: audited={stats['audited']}, low_quality(<0.35)={stats['low_quality']}, no_vector={stats['no_vector']}")
    return stats


# ---------------------------------------------------------------------------
# Phase 3 — embedding_repair (Groq calls)
# ---------------------------------------------------------------------------

def _build_groq_client():
    """Mirror the construction logic used in MovieService / RSSService."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None
    if os.getenv("GROQ_API_KEY"):
        return AsyncOpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
            max_retries=0,
        )
    if os.getenv("GEMINI_API_KEY"):
        return AsyncOpenAI(
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    return None


async def phase_embedding_repair(limit: int, dry_run: bool) -> dict:
    """Re-enrich movies flagged as low quality or never LLM-enriched."""
    from services.cinematic_enricher import DailyLimitExhausted

    stats = {"queued": 0, "repaired": 0, "failed": 0, "stopped_early": False}

    async with AsyncSessionLocal() as db:
        query = (
            select(Movie)
            .where(
                or_(
                    Movie.has_enriched_embedding.is_(False),
                    Movie.embedding_quality_score < 0.35,
                )
            )
            .order_by(Movie.popularity.desc().nullslast())
            .limit(limit)
        )
        movies = (await db.execute(query)).scalars().all()
        stats["queued"] = len(movies)
        logger.info(f"[Phase 3] Queued {len(movies)} movies for re-enrichment")

        if dry_run or not movies:
            return stats

        groq = _build_groq_client()
        if groq is None:
            logger.warning("[Phase 3] No GROQ/GEMINI key — skipping repair phase")
            return stats

        qdrant = QdrantService()
        embedding_service = EmbeddingService()
        try:
            for movie in movies:
                try:
                    ok = await _re_enrich_movie(movie, groq, qdrant, embedding_service)
                except DailyLimitExhausted as e:
                    logger.warning(f"[Phase 3] Groq daily limit hit ({e}). Stopping early.")
                    stats["stopped_early"] = True
                    break
                except Exception as e:
                    logger.warning(f"[Phase 3] Failed {movie.title}: {e}")
                    stats["failed"] += 1
                    continue
                if ok:
                    stats["repaired"] += 1
                else:
                    stats["failed"] += 1

                if stats["repaired"] % 25 == 0 and stats["repaired"] > 0:
                    await db.commit()
            await db.commit()
        finally:
            try:
                await groq.close()
            except Exception:
                pass

    logger.info(f"[Phase 3] Done: repaired={stats['repaired']}, failed={stats['failed']}, stopped_early={stats['stopped_early']}")
    return stats


# ---------------------------------------------------------------------------
# Phase 4 — backfill_descriptions
# ---------------------------------------------------------------------------

async def phase_backfill_descriptions(limit: int, dry_run: bool) -> dict:
    """Fill cinematic_description for movies that have an enriched embedding but no description."""
    from services.cinematic_enricher import generate_cinematic_description, DailyLimitExhausted

    stats = {"queued": 0, "filled": 0, "failed": 0, "stopped_early": False}

    async with AsyncSessionLocal() as db:
        query = (
            select(Movie)
            .where(Movie.has_enriched_embedding.is_(True))
            .where(or_(Movie.cinematic_description.is_(None), Movie.cinematic_description == ""))
            .order_by(Movie.popularity.desc().nullslast())
            .limit(limit)
        )
        movies = (await db.execute(query)).scalars().all()
        stats["queued"] = len(movies)
        logger.info(f"[Phase 4] Queued {len(movies)} movies for description backfill")

        if dry_run or not movies:
            return stats

        groq = _build_groq_client()
        if groq is None:
            logger.warning("[Phase 4] No GROQ/GEMINI key — skipping backfill phase")
            return stats

        try:
            for movie in movies:
                try:
                    desc, model_used = await generate_cinematic_description(
                        title=movie.title or "",
                        overview=movie.overview or "",
                        genres=movie.genres or [],
                        keywords=movie.keywords or [],
                        directors=movie.directors or [],
                        cast=movie.cast or [],
                        year=movie.year or 0,
                        groq_client=groq,
                    )
                except DailyLimitExhausted as e:
                    logger.warning(f"[Phase 4] Groq daily limit hit. Stopping early.")
                    stats["stopped_early"] = True
                    break

                if desc and model_used is not None:
                    movie.cinematic_description = desc
                    stats["filled"] += 1
                else:
                    stats["failed"] += 1

                if stats["filled"] % 25 == 0 and stats["filled"] > 0:
                    await db.commit()
            await db.commit()
        finally:
            try:
                await groq.close()
            except Exception:
                pass

    logger.info(f"[Phase 4] Done: filled={stats['filled']}, failed={stats['failed']}, stopped_early={stats['stopped_early']}")
    return stats


# ---------------------------------------------------------------------------
# Phase 5 — reset_profiles
# ---------------------------------------------------------------------------

async def phase_reset_profiles(dry_run: bool) -> dict:
    stats = {"clusters_rebuilt": 0}
    if dry_run:
        logger.info("[Phase 5] DRY-RUN — would re-cluster all users with onboarding completed")
        return stats

    from sqlalchemy import delete
    from models.database import User, UserCluster
    from services.clustering_service import ClusteringService

    qdrant = QdrantService()
    groq = _build_groq_client()
    clustering = ClusteringService(qdrant=qdrant)

    async with AsyncSessionLocal() as db:
        users = (await db.execute(
            select(User).where(User.onboarding_completed.is_(True))
        )).scalars().all()
        logger.info(f"[Phase 5] Found {len(users)} users to re-cluster")

        # Wipe all clusters once, then rebuild per-user
        await db.execute(delete(UserCluster))
        await db.commit()

        for u in users:
            try:
                await clustering.create_user_clusters(u.id, db, groq_client=groq)
                stats["clusters_rebuilt"] += 1
            except Exception as e:
                logger.warning(f"[Phase 5] Cluster rebuild failed for user {u.id}: {e}")

    if groq is not None:
        try:
            await groq.close()
        except Exception:
            pass

    logger.info(f"[Phase 5] Done: clusters_rebuilt={stats['clusters_rebuilt']}")
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

PHASE_FNS = {
    1: ("refresh_metadata", phase_refresh_metadata),
    2: ("embedding_audit", phase_embedding_audit),
    3: ("embedding_repair", phase_embedding_repair),
    4: ("backfill_descriptions", phase_backfill_descriptions),
    5: ("reset_profiles", phase_reset_profiles),
}


async def run(phases: List[int], omdb_budget: int, embed_limit: int, dry_run: bool) -> None:
    started = datetime.utcnow()
    logger.info(f"=== Maintenance Orchestrator started at {started.isoformat()}Z ===")
    logger.info(f"Phases: {phases}  omdb_budget={omdb_budget}  embed_limit={embed_limit}  dry_run={dry_run}")

    summary = {}
    for ph in phases:
        name, fn = PHASE_FNS[ph]
        logger.info(f"\n--- Running phase {ph}: {name} ---")
        try:
            if ph == 1:
                summary[name] = await fn(omdb_budget=omdb_budget, dry_run=dry_run)
            elif ph in (2, 3, 4):
                summary[name] = await fn(limit=embed_limit, dry_run=dry_run)
            else:
                summary[name] = await fn(dry_run=dry_run)
        except Exception as e:
            logger.error(f"Phase {ph} ({name}) crashed: {e}")
            summary[name] = {"error": str(e)}

    elapsed = (datetime.utcnow() - started).total_seconds()
    logger.info(f"\n=== Done in {elapsed:.0f}s ===")
    for name, stats in summary.items():
        logger.info(f"  {name}: {stats}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--phases",
        type=str,
        default="1,2,3,4,5",
        help="Comma-separated phase numbers to run (default: all)",
    )
    parser.add_argument(
        "--omdb-budget",
        type=int,
        default=DEFAULT_OMDB_DAILY_LIMIT,
        help=f"Max OMDb calls for this run (capped by remaining daily limit). Default: {DEFAULT_OMDB_DAILY_LIMIT}",
    )
    parser.add_argument(
        "--embed-limit",
        type=int,
        default=500,
        help="Max movies per embedding phase (audit/repair/backfill). Default: 500",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without writing")

    args = parser.parse_args()
    phases = [int(p.strip()) for p in args.phases.split(",") if p.strip()]
    invalid = [p for p in phases if p not in PHASE_FNS]
    if invalid:
        parser.error(f"Invalid phase numbers: {invalid}. Valid: 1-5")

    asyncio.run(run(phases, args.omdb_budget, args.embed_limit, args.dry_run))


if __name__ == "__main__":
    main()
