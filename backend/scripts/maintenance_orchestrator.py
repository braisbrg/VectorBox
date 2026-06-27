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
    6. recalc_vbs          — recomputes vectorbox_score for every movie using
                             existing DB columns (no API). Catches the films
                             Phase 1 didn't touch today, and propagates any
                             VBS-formula change to the whole catalog in seconds.
    7. vector_presence_check — diffs Postgres movies vs Qdrant point IDs and
                             regenerates embeddings for the missing ones from
                             stored `cinematic_description` (or overview+genres
                             fallback). No external APIs. Replaces the legacy
                             heal_vectors.py time-window script.
    8. popular_refresh     — refreshes the "Popular on Letterboxd" Redis
                             cache. Letterboxd first (curl_cffi + slug cache);
                             falls back to Trakt /movies/trending if the
                             scrape returns < threshold IDs. Replaces the
                             legacy popular_scraper.py cron script.

OMDb budget is tracked in the `api_budget` table (100k/day default — Patron tier).
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
import time
from datetime import date, datetime, timedelta
from typing import List, Optional

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

from sqlalchemy import select, func, or_, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import AsyncSessionLocal
from models.database import Movie, ApiBudget
from models.external_schemas import OMDbResponse
from services.tmdb_client import TMDBClient
from services.omdb_client import OMDbClient
from services.qdrant_service import QdrantService
from services.embedding_service import EmbeddingService

# Reuse existing single-movie helpers
from scripts.refresh_metadata import refresh_movie, mark_released_upcoming
from scripts.check_embeddings import check_movie_embedding, _re_enrich_movie
from scripts.reembed_catalog import _build_text as _embed_text, _qdrant_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("maintenance")


class _Progress:
    """Throttled progress logger for the long per-item phase loops.

    Emits one timestamped line at most every `min_interval` seconds (plus a
    guaranteed final line at completion), so you can see a phase is alive
    without spamming the log. Time-based rather than every-N-items so it
    auto-adapts to both fast (recalc_vbs, thousands/s) and slow (Groq, ~1/s)
    phases. Non-TTY friendly: plain INFO lines, no carriage-return bars.
    """

    def __init__(self, label: str, total: int, min_interval: float = 10.0):
        self.label = label
        self.total = total
        self.min_interval = min_interval
        self.t0 = time.monotonic()
        self._last = self.t0

    def step(self, done: int) -> None:
        now = time.monotonic()
        if done < self.total and (now - self._last) < self.min_interval:
            return
        self._last = now
        elapsed = now - self.t0
        rate = done / elapsed if elapsed > 0 else 0.0
        eta_min = ((self.total - done) / rate / 60) if rate > 0 else 0.0
        pct = (100 * done / self.total) if self.total else 100.0
        logger.info(
            f"{self.label} {done}/{self.total} ({pct:.0f}%) · {rate:.1f}/s · ETA {eta_min:.1f}m"
        )


# ---------------------------------------------------------------------------
# OMDb budget helpers (api_budget table)
# ---------------------------------------------------------------------------

DEFAULT_OMDB_DAILY_LIMIT = 100_000  # OMDb Patron tier (paid, $1/mo). Free tier was 1000.


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

REFRESH_STALE_DAYS = 7   # OMDb Patron tier (100k/day) makes weekly sweeps cheap.
NO_OMDB_RETRY_DAYS = 30  # Aligns with OMDb negative-cache TTL.


async def phase_refresh_metadata(omdb_budget: int, dry_run: bool) -> dict:
    """
    Targets movies that either:
      - have imdb_id and have never been refreshed, OR
      - are healthy (imdb_vote_count populated) and stale > REFRESH_STALE_DAYS, OR
      - have no IMDb vote_count AND were last retried > NO_OMDB_RETRY_DAYS ago
        (OMDb genuinely doesn't cover ~5% of recent/obscure films; retrying
        them daily was a no-op loop — the OMDb negative-cache TTL is 30d so
        a monthly retry is the natural rhythm to catch new coverage).
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

        stale_cutoff = datetime.utcnow() - timedelta(days=REFRESH_STALE_DAYS)
        no_omdb_cutoff = datetime.utcnow() - timedelta(days=NO_OMDB_RETRY_DAYS)
        query = (
            select(Movie)
            .where(Movie.imdb_id.isnot(None))
            .where(
                or_(
                    # Never been refreshed — first attempt, highest priority
                    Movie.last_metadata_refresh.is_(None),
                    # Healthy films with vote_count: weekly cadence
                    and_(
                        Movie.imdb_vote_count.isnot(None),
                        Movie.last_metadata_refresh < stale_cutoff,
                    ),
                    # OMDb coverage holes (NULL vote_count): retry monthly,
                    # not daily, so we stop burning TMDB calls on films that
                    # OMDb has no record of. The 30d cadence matches the
                    # OMDb negative-cache TTL.
                    and_(
                        Movie.imdb_vote_count.is_(None),
                        Movie.last_metadata_refresh < no_omdb_cutoff,
                    ),
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
        prog = _Progress("[Phase 1]", len(movies))
        try:
            for i, movie in enumerate(movies, 1):
                ok = await refresh_movie(movie, tmdb, omdb)
                if ok:
                    stats["refreshed"] += 1
                else:
                    stats["failed"] += 1
                stats["omdb_used"] += 1  # refresh_movie always calls OMDb (when imdb_id set)

                # Persist progress every 25 movies (resilient to interruption)
                if stats["refreshed"] % 25 == 0 and stats["refreshed"] > 0:
                    await db.commit()
                prog.step(i)

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
    """Populate embedding_quality_score for movies that have NULL.

    No external API — only local embeddinggemma inference (~15ms/film). The
    `limit` arg is the `--audit-limit` from the orchestrator (default 20000,
    well above current catalog size) and is intentionally NOT tied to
    `--embed-limit` (which gates Groq-budget-bound phases 3/4). A full
    catalog sweep at 10k films takes ~3 min.
    """
    stats = {"queued": 0, "audited": 0, "low_quality": 0, "no_vector": 0, "remaining": 0}

    async with AsyncSessionLocal() as db:
        base_filter = [
            Movie.embedding_quality_score.is_(None),
            Movie.has_enriched_embedding.is_(True),
        ]
        query = (
            select(Movie)
            .where(*base_filter)
            .order_by(Movie.popularity.desc().nullslast())
            .limit(limit)
        )
        movies = (await db.execute(query)).scalars().all()
        stats["queued"] = len(movies)
        logger.info(f"[Phase 2] Queued {len(movies)} movies for embedding audit (limit={limit})")

        if dry_run or not movies:
            # Report how many would still be pending even without processing,
            # so a dry-run on a freshly-seeded catalog is informative.
            total_pending = (await db.execute(
                select(func.count(Movie.id)).where(*base_filter)
            )).scalar_one()
            stats["remaining"] = max(0, total_pending - stats["queued"])
            if dry_run:
                logger.info(f"[Phase 2] DRY-RUN remaining beyond limit: {stats['remaining']}")
            return stats

        stats["unmeasurable"] = 0
        qdrant = QdrantService()
        embedding_service = EmbeddingService()
        prog = _Progress("[Phase 2]", len(movies))
        try:
            for i, movie in enumerate(movies, 1):
                prog.step(i)
                # Skip the cosine check when the reference text would be too
                # thin to be meaningful. Without a real overview the reference
                # is just "Genres: X. Cast: Y" — generic enough to score low
                # against ANY cinematic_description, looping Phase 3 forever
                # without ever fixing anything. Marking these with 1.0
                # ("assumed OK — can't measure") closes the loop while staying
                # honest: we don't claim quality, we claim absence of signal.
                if not movie.overview or len(movie.overview.strip()) < 100:
                    movie.embedding_quality_score = 1.0
                    stats["unmeasurable"] += 1
                    continue

                quality = await check_movie_embedding(movie, qdrant, embedding_service)
                if quality is None:
                    stats["no_vector"] += 1
                    continue
                movie.embedding_quality_score = quality
                if quality < 0.35:
                    stats["low_quality"] += 1
                stats["audited"] += 1

                if (stats["audited"] + stats["unmeasurable"]) % 50 == 0:
                    await db.commit()
            await db.commit()
        finally:
            pass  # services manage their own lifecycle

        # Post-run count: surface partial sweeps loudly so they don't look
        # like "all done" when in reality only the first N got audited.
        total_pending = (await db.execute(
            select(func.count(Movie.id)).where(*base_filter)
        )).scalar_one()
        stats["remaining"] = total_pending

    if stats["remaining"]:
        logger.warning(
            f"[Phase 2] Done: audited={stats['audited']}, low_quality(<0.35)={stats['low_quality']}, "
            f"no_vector={stats['no_vector']} — STILL UNAUDITED: {stats['remaining']} "
            f"(raise --audit-limit or re-run Phase 2)"
        )
    else:
        logger.info(
            f"[Phase 2] Done: audited={stats['audited']}, low_quality(<0.35)={stats['low_quality']}, "
            f"no_vector={stats['no_vector']} — fully drained"
        )
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
            timeout=40.0,  # REL-4: bound calls (SDK default 600s)
        )
    if os.getenv("GEMINI_API_KEY"):
        return AsyncOpenAI(
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            timeout=40.0,  # REL-4
        )
    return None


# The standardised enrichment chain (2026-06 model sweep): qwen3-32b is the most
# consistent free model for V2 descriptions, gpt-oss-120b the richest. Phase 3
# uses ONLY these two so a full 8-phase run stays consistent with the bulk
# re-enrich (scripts/enrich_vectors.py --chain qwen3-32b,oss-120).
REENRICH_CHAIN = ["qwen/qwen3-32b", "openai/gpt-oss-120b"]


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
        prog = _Progress("[Phase 3]", len(movies))
        try:
            for i, movie in enumerate(movies, 1):
                prog.step(i)
                try:
                    ok = await _re_enrich_movie(
                        movie, groq, qdrant, embedding_service,
                        model_chain_override=REENRICH_CHAIN,
                    )
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
                    # B-21 fix (2026-05-15): the re-enriched vector is now in
                    # Qdrant, but Movie.embedding_quality_score still holds the
                    # stale low value. Recompute against the fresh reference
                    # text so the next Phase 2/3 doesn't re-flag the same film
                    # forever. Failures here are non-fatal — repair already
                    # succeeded; quality_score will just stay stale.
                    try:
                        new_score = await check_movie_embedding(
                            movie, qdrant, embedding_service
                        )
                        if new_score is not None:
                            movie.embedding_quality_score = new_score
                    except Exception as e:
                        logger.warning(
                            f"[Phase 3] quality_score recheck failed for {movie.title}: {e}"
                        )
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

        prog = _Progress("[Phase 4]", len(movies))
        try:
            for i, movie in enumerate(movies, 1):
                prog.step(i)
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
    """Re-cluster every user that has at least one rating.

    Criterion change (2026-05-15): previously filtered by `onboarding_completed=True`,
    which excluded all real users — the flag is only flipped by the carousel
    `/rate` endpoint at ≥15 ratings, never by the ZIP/RSS import paths even
    though those users have thousands of ratings (B-20). Now matches
    `scripts/reset_profiles.py`: anyone with a rating gets re-clustered.

    Order change: the DELETE on `user_clusters` now happens only AFTER we've
    confirmed there are users to rebuild — previously, an empty filter result
    left the DB clusterless and never rebuilt (B-19).
    """
    stats = {"clusters_rebuilt": 0, "users_found": 0}
    if dry_run:
        logger.info("[Phase 5] DRY-RUN — would re-cluster every user with at least one rating")
        return stats

    from sqlalchemy import delete
    from models.database import User, UserCluster, UserRating
    from services.clustering_service import ClusteringService

    qdrant = QdrantService()
    groq = _build_groq_client()
    clustering = ClusteringService(qdrant=qdrant)

    async with AsyncSessionLocal() as db:
        # Same selector as scripts/reset_profiles.py — any user with a rating.
        # Onboarding flag is not gating maintenance; we re-cluster real users
        # regardless of how their ratings got in (carousel / ZIP / RSS).
        user_ids = (await db.execute(
            select(User.id)
            .join(UserRating, User.id == UserRating.user_id)
            .distinct()
        )).scalars().all()
        stats["users_found"] = len(user_ids)
        logger.info(f"[Phase 5] Found {len(user_ids)} users to re-cluster")

        if not user_ids:
            # No-op — do NOT wipe existing clusters when we have nothing to
            # rebuild (pre-fix Phase 5 would have left the DB clusterless).
            logger.info("[Phase 5] No users to re-cluster, skipping cluster wipe.")
            return stats

        # Safe to wipe — at least one rebuild will follow.
        await db.execute(delete(UserCluster))
        await db.commit()

        prog = _Progress("[Phase 5]", len(user_ids))
        for i, uid in enumerate(user_ids, 1):
            try:
                await clustering.create_user_clusters(uid, db, groq_client=groq)
                stats["clusters_rebuilt"] += 1
            except Exception as e:
                logger.warning(f"[Phase 5] Cluster rebuild failed for user {uid}: {e}")
            prog.step(i)

    if groq is not None:
        try:
            await groq.close()
        except Exception:
            pass

    logger.info(f"[Phase 5] Done: clusters_rebuilt={stats['clusters_rebuilt']}/{stats['users_found']}")
    return stats


# ---------------------------------------------------------------------------
# Phase 6 — recalc_vbs (no external API)
# ---------------------------------------------------------------------------

async def phase_recalc_vbs(dry_run: bool) -> dict:
    """Recompute vectorbox_score for every movie from existing DB columns.

    Mirrors scripts/recalc_vbs_from_db.py — bypasses OMDbClient.__init__ to
    avoid HTTP setup and feeds the calculator a synthetic OMDbResponse built
    from the columns we already store. Catches the two cases Phase 1 misses:
      - films not due for refresh today (Phase 1 selector only picks rows
        with NULL imdb_vote_count or stale >30d last_metadata_refresh),
      - VBS-formula changes that need to propagate to the whole catalog.
    """
    stats = {"total": 0, "updated": 0, "cleared": 0, "unchanged": 0}

    def _synthetic_omdb(m: Movie) -> OMDbResponse:
        return OMDbResponse(
            Response="True",
            imdbRating=str(m.imdb_rating) if m.imdb_rating is not None else None,
            Metascore=str(m.metacritic_rating) if m.metacritic_rating is not None else None,
            imdbVotes=str(m.imdb_vote_count) if m.imdb_vote_count else None,
        )

    omdb = OMDbClient.__new__(OMDbClient)  # bypass __init__ — no API calls

    async with AsyncSessionLocal() as db:
        movies = (await db.execute(select(Movie).order_by(Movie.id))).scalars().all()
        stats["total"] = len(movies)
        logger.info(f"[Phase 6] Recalculating VBS for {stats['total']} movies (DB-only)")

        if dry_run or not movies:
            return stats

        prog = _Progress("[Phase 6]", len(movies))
        for i, m in enumerate(movies, 1):
            prog.step(i)
            previous = m.vectorbox_score
            vb = omdb.calculate_vectorbox_score(
                _synthetic_omdb(m),
                m.vote_average,
                tmdb_vote_count=m.vote_count,
                imdb_vote_count=m.imdb_vote_count,
            )

            if vb.score is None:
                if previous is not None:
                    m.vectorbox_score = None
                    stats["cleared"] += 1
                else:
                    stats["unchanged"] += 1
                continue

            if previous is None or abs((previous or 0) - vb.score) > 0.05:
                m.vectorbox_score = vb.score
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1

            if i % 500 == 0:
                await db.commit()

        await db.commit()

    logger.info(f"[Phase 6] Done: updated={stats['updated']} cleared={stats['cleared']} unchanged={stats['unchanged']}")
    return stats


# ---------------------------------------------------------------------------
# Phase 7 — vector_presence_check (no external API)
# ---------------------------------------------------------------------------

async def phase_vector_presence(dry_run: bool) -> dict:
    """Re-upsert embeddings for films present in Postgres but missing from Qdrant.

    Replaces scripts/heal_vectors.py — that script targeted a 24h time window
    AND re-called Groq for every match, which was an expensive answer to a
    cheaper problem. Most genuine "healing" cases are silently-dropped Qdrant
    upserts where the source text is already in DB; re-encoding from
    `cinematic_description` (or the overview/genres fallback) closes the gap
    without spending Groq quota. Films that have neither a vector nor any
    usable source text are reported as `skipped_no_text` and remain the
    responsibility of Phase 3 (which would re-enrich them via Groq).
    """
    stats = {
        "db_total": 0,
        "qdrant_total": 0,
        "missing": 0,
        "upserted": 0,
        "skipped_no_text": 0,
        "failed": 0,
    }

    qdrant = QdrantService()

    async with AsyncSessionLocal() as db:
        movies = (await db.execute(select(Movie).order_by(Movie.id))).scalars().all()
        stats["db_total"] = len(movies)

        # Scroll all Qdrant point IDs in pages — payload + vectors disabled to
        # keep the membership check cheap (we only need the ID set).
        qdrant_ids: set[int] = set()
        next_offset = None
        while True:
            points, next_offset = await qdrant.client.scroll(
                collection_name=QdrantService.COLLECTION_NAME,
                limit=10_000,
                offset=next_offset,
                with_payload=False,
                with_vectors=False,
            )
            qdrant_ids.update(p.id for p in points)
            if next_offset is None:
                break
        stats["qdrant_total"] = len(qdrant_ids)

        missing_movies = [m for m in movies if m.tmdb_id not in qdrant_ids]
        stats["missing"] = len(missing_movies)
        logger.info(
            f"[Phase 7] DB={stats['db_total']} Qdrant={stats['qdrant_total']} missing={stats['missing']}"
        )

        if dry_run or not missing_movies:
            return stats

        from services.embedding_service import get_model
        model = get_model()
        batch_size = 64

        for i in range(0, len(missing_movies), batch_size):
            chunk = missing_movies[i : i + batch_size]
            texts, ready = [], []
            for m in chunk:
                t = _embed_text(m)
                if not t:
                    stats["skipped_no_text"] += 1
                    continue
                texts.append(t)
                ready.append(m)

            if not texts:
                continue

            vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
            for m, v in zip(ready, vectors):
                try:
                    await qdrant.upsert_movie_vector(
                        movie_id=m.tmdb_id,
                        vector=v.tolist(),
                        metadata=_qdrant_payload(m),
                    )
                    stats["upserted"] += 1
                except Exception as e:
                    logger.warning(f"[Phase 7] Upsert failed for {m.title}: {e}")
                    stats["failed"] += 1

            logger.info(
                f"[Phase 7] {min(i + batch_size, len(missing_movies))}/{len(missing_movies)} processed "
                f"(upserted={stats['upserted']}, skipped_no_text={stats['skipped_no_text']}, failed={stats['failed']})"
            )

    logger.info(
        f"[Phase 7] Done: upserted={stats['upserted']} skipped_no_text={stats['skipped_no_text']} failed={stats['failed']}"
    )
    return stats


# ---------------------------------------------------------------------------
# Phase 8 — popular_refresh (Letterboxd scrape with Trakt fallback)
# ---------------------------------------------------------------------------

async def phase_popular_refresh(dry_run: bool) -> dict:
    """Refresh the 'Popular on Letterboxd' Redis cache.

    Uses the consolidated `ScraperService.get_popular_with_fallback()` —
    Letterboxd first (curl_cffi Chrome impersonation + slug cache), Trakt
    `/movies/trending` if the Letterboxd scrape returns < threshold IDs.
    Writes a JSON array to `cache:{FEED_CACHE_VERSION}:popular_letterboxd:ids`
    with 24h TTL. Same key/value/TTL as the legacy `popular_scraper.py`
    cron script — `TrendingService.get_popular_movie_ids` reads it.

    No DB writes, no OMDb/Groq quota. Safe to run daily.
    """
    import json as _json
    import os
    import redis.asyncio as aioredis
    from services.scraper_service import ScraperService
    from services.trending_service import POPULAR_IDS_KEY

    stats = {"slugs_resolved": 0, "with_rating": 0, "source": None, "cached": False}

    scraper = ScraperService()
    try:
        if dry_run:
            logger.info(f"[Phase 8] DRY-RUN would scrape popular + write to Redis key={POPULAR_IDS_KEY}")
            return stats

        items, source = await scraper.get_popular_with_fallback(min_items=20)
        stats["slugs_resolved"] = len(items)
        stats["with_rating"] = sum(1 for it in items if it.get("letterboxd_rating") is not None)
        stats["source"] = source

        if not items:
            logger.warning("[Phase 8] both Letterboxd and Trakt produced 0 items — leaving Redis cache untouched")
            return stats

        redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            # Schema: [{"tmdb_id": int, "letterboxd_rating": float|None}, ...]
            # TrendingService normalizes back to List[int] for legacy callers
            # AND exposes the full items via get_popular_movie_items().
            await r.set(POPULAR_IDS_KEY, _json.dumps(items), ex=24 * 60 * 60)
            stats["cached"] = True
            logger.info(
                f"[Phase 8] cached {len(items)} items at {POPULAR_IDS_KEY} "
                f"(source={source}, with_rating={stats['with_rating']}, TTL 24h)"
            )
        finally:
            await r.close()
    finally:
        await scraper.close()

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
    6: ("recalc_vbs", phase_recalc_vbs),
    7: ("vector_presence_check", phase_vector_presence),
    8: ("popular_refresh", phase_popular_refresh),
}


async def run(phases: List[int], omdb_budget: int, embed_limit: int, audit_limit: int, dry_run: bool) -> None:
    started = datetime.utcnow()
    logger.info(f"=== Maintenance Orchestrator started at {started.isoformat()}Z ===")
    logger.info(f"Phases: {phases}  omdb_budget={omdb_budget}  embed_limit={embed_limit}  audit_limit={audit_limit}  dry_run={dry_run}")

    summary = {}
    for ph in phases:
        name, fn = PHASE_FNS[ph]
        logger.info(f"\n--- Running phase {ph}: {name} ---")
        try:
            if ph == 1:
                summary[name] = await fn(omdb_budget=omdb_budget, dry_run=dry_run)
            elif ph == 2:
                # Audit has no API cost — independent (much higher) cap.
                summary[name] = await fn(limit=audit_limit, dry_run=dry_run)
            elif ph in (3, 4):
                summary[name] = await fn(limit=embed_limit, dry_run=dry_run)
            else:  # 5, 6, 7, 8 — only dry_run
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
        default="1,2,3,4,5,6,7,8",
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
        help="Max movies per Groq-bound embedding phase (3 repair, 4 backfill). Default: 500",
    )
    parser.add_argument(
        "--audit-limit",
        type=int,
        default=20000,
        help="Max movies per Phase 2 embedding audit (no API — only local CPU). "
             "Default 20000 ≈ 2x current catalog, raise if seed grows past that. ~15ms/film.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without writing")

    args = parser.parse_args()
    phases = [int(p.strip()) for p in args.phases.split(",") if p.strip()]
    invalid = [p for p in phases if p not in PHASE_FNS]
    if invalid:
        parser.error(f"Invalid phase numbers: {invalid}. Valid: 1-8")

    asyncio.run(run(phases, args.omdb_budget, args.embed_limit, args.audit_limit, args.dry_run))


if __name__ == "__main__":
    main()
