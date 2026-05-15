from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import List, Dict
from pydantic import BaseModel, conlist, constr
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import User, Movie, UserRating
from models.schemas import TokenResponse
from services.movie_service import MovieService
from services.rss_service import RSSService
from services.scraper_service import ScraperService
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from config import get_db
from dependencies import get_tmdb_client, get_current_user, get_qdrant_service
from limiter import limiter
import logging
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger(__name__)


# Fuzzy-match gate thresholds for the watchlist-scrape fallback. Conservative
# because a false positive lands the wrong film in a user's watchlist (see the
# 2026-05-10 user-212 phantom "Samuel and the Light" incident).
#
# Two-tier acceptance — the higher tier exists so legitimate ultra-indie
# cinema (cine galego/español de festival, 1-5 TMDB votes) is not rejected
# along with the phantoms. Phantoms always fail title similarity because
# the scraped title doesn't match the TMDB top-1.
_FUZZY_TITLE_RATIO_STRICT = 0.95    # near-exact title match → bypass vote_count
_FUZZY_TITLE_RATIO_MIN = 0.85       # weaker match still requires vote_count gate
_FUZZY_VOTE_COUNT_MIN = 20          # below 20 the candidate must clear the strict ratio
_FUZZY_YEAR_TOLERANCE = 1           # ± years


def _normalise_for_title_match(text: str) -> str:
    """Lowercase, strip non-alphanumerics. Used for fuzzy title comparison
    so 'It's a Wonderful Life' matches 'Its a Wonderful Life' and en/em
    dashes don't sabotage the ratio."""
    return "".join(ch.lower() for ch in (text or "") if ch.isalnum() or ch.isspace()).strip()


def _accept_fuzzy_match(
    candidate: dict,
    scraped_title: Optional[str],
    scraped_year: Optional[int],
    slug: str,
) -> bool:
    """Two-tier acceptance gate for the TMDB top-1 fuzzy hit. Logs the verdict
    either way so future watchlist false positives are traceable.

    Order of checks: year match first (cheapest reject), then scraped_title
    present (gate cannot run without it), then title-similarity tier decides
    whether vote_count matters.

    Tier A — strict title match (ratio ≥ 0.95) bypasses vote_count.
        Covers legitimate ultra-indie cinema (1-vote festival entries,
        Galician/Spanish indies, etc.) where the title matches near-exactly
        but the film has too few TMDB votes for the standard gate.

    Tier B — weak title match (0.85 ≤ ratio < 0.95) still requires
        vote_count ≥ 20. Catches phantoms where TMDB's relevance ranker
        promoted an unrelated low-vote film to the top.
    """
    cand_title = candidate.get("title") or ""
    cand_orig = candidate.get("original_title") or ""
    cand_release = candidate.get("release_date") or ""
    cand_year = int(cand_release[:4]) if len(cand_release) >= 4 and cand_release[:4].isdigit() else None
    cand_votes = candidate.get("vote_count") or 0
    cand_pop = candidate.get("popularity") or 0.0

    # Year (within tolerance, when both available)
    if scraped_year and cand_year and abs(scraped_year - cand_year) > _FUZZY_YEAR_TOLERANCE:
        logger.warning(
            f"[watchlist-fuzzy] REJECT slug={slug!r} reason=year_mismatch "
            f"scraped_year={scraped_year} cand_year={cand_year} cand_title={cand_title!r}"
        )
        return False

    # Title similarity — required gate. If we don't have a scraped title
    # (legacy poster layout), we cannot run the gate.
    if not scraped_title:
        logger.warning(
            f"[watchlist-fuzzy] REJECT slug={slug!r} reason=no_scraped_title "
            f"(legacy poster layout); fuzzy cannot be verified"
        )
        return False

    scraped_norm = _normalise_for_title_match(scraped_title)
    best_ratio = max(
        SequenceMatcher(None, scraped_norm, _normalise_for_title_match(cand_title)).ratio(),
        SequenceMatcher(None, scraped_norm, _normalise_for_title_match(cand_orig)).ratio(),
    )

    if best_ratio >= _FUZZY_TITLE_RATIO_STRICT:
        logger.info(
            f"[watchlist-fuzzy] ACCEPT(strict) slug={slug!r} cand_id={candidate.get('id')} "
            f"cand_title={cand_title!r} year={cand_year} votes={cand_votes} title_ratio={best_ratio:.2f}"
        )
        return True

    if best_ratio < _FUZZY_TITLE_RATIO_MIN:
        logger.warning(
            f"[watchlist-fuzzy] REJECT slug={slug!r} reason=title_drift "
            f"scraped={scraped_title!r} cand={cand_title!r} ratio={best_ratio:.2f}"
        )
        return False

    # Tier B — weak match still requires vote_count floor.
    if cand_votes < _FUZZY_VOTE_COUNT_MIN:
        logger.warning(
            f"[watchlist-fuzzy] REJECT slug={slug!r} reason=weak_title_and_low_votes "
            f"votes={cand_votes} popularity={cand_pop:.3f} cand_title={cand_title!r} ratio={best_ratio:.2f}"
        )
        return False

    logger.info(
        f"[watchlist-fuzzy] ACCEPT(weak) slug={slug!r} cand_id={candidate.get('id')} "
        f"cand_title={cand_title!r} year={cand_year} votes={cand_votes} title_ratio={best_ratio:.2f}"
    )
    return True

router = APIRouter(
    tags=["rss"],
    responses={404: {"description": "Not found"}},
)

class SyncResponse(BaseModel):
    status: str
    stats: Dict[str, int]
    message: str

class GroupVibeRequest(BaseModel):
    usernames: conlist(constr(min_length=1, max_length=80), min_length=2, max_length=8)

async def _invalidate_feed_cache(user_id: int) -> None:
    """Delete all cached feed keys for this user after RSS sync or upload."""
    try:
        import redis.asyncio as aioredis
        import os

        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379")
        r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            from services.feed_service import FEED_CACHE_VERSION
            deleted_count = 0
            # Sweep all key patterns that encode user-specific feed state
            patterns = [
                f"section:{FEED_CACHE_VERSION}:{user_id}:*",
                f"signal_cache:{user_id}:*",
            ]
            for pattern in patterns:
                cursor = 0
                while True:
                    cursor, keys = await r.scan(cursor, match=pattern, count=100)
                    if keys:
                        await r.delete(*keys)
                        deleted_count += len(keys)
                    if cursor == 0:
                        break
            # Delete cluster rotation counter
            await r.delete(f"cluster_rotation:{FEED_CACHE_VERSION}:{user_id}")

            if deleted_count:
                logger.info(f"Invalidated {deleted_count} feed/signal cache keys and rotation for user_id={user_id}")
        finally:
            await r.close()
    except Exception as e:
        logger.error(f"Feed cache invalidation failed for user_id={user_id}: {e}")

async def _run_sync_background(user_id: int, letterboxd_profile: str, tmdb: TMDBClient) -> None:
    """Background task — owns its own session. Never re-raises."""
    from config import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            rss_service = RSSService(db, tmdb=tmdb)
            await rss_service.sync_user_rss(letterboxd_profile, user_id)

            scraper = ScraperService()
            movie_service = MovieService(db, tmdb=tmdb)
            watchlist_added = 0

            try:
                watchlist_items = await scraper.scrape_watchlist_recent(letterboxd_profile)

                for item in watchlist_items:
                    film_slug = item["film_slug"]
                    film_year = item.get("year")
                    film_title = item.get("title")  # from data-item-name; preserves accents/punct

                    page_tmdb_id = await scraper.get_tmdb_id(film_slug)
                    tmdb_id = page_tmdb_id
                    if tmdb_id:
                        logger.info(
                            f"[watchlist-resolve] PAGE slug={film_slug!r} tmdb_id={tmdb_id} title={film_title!r}"
                        )
                    else:
                        # Fuzzy fallback. Query with the scraped title (richer than
                        # the slug — keeps accents and punctuation) when available;
                        # only fall back to slug-derived text if the title is
                        # missing (old poster layout).
                        query = film_title or film_slug.replace("-", " ")
                        params = {"query": query}
                        if film_year:
                            params["year"] = film_year
                        logger.info(
                            f"[watchlist-resolve] PAGE_MISS slug={film_slug!r} trying fuzzy "
                            f"query={query!r} year={film_year}"
                        )
                        tmdb_results = await tmdb._make_request("/search/movie", params)
                        candidate = None
                        if tmdb_results and tmdb_results.get("results"):
                            candidate = tmdb_results["results"][0]
                        if candidate and _accept_fuzzy_match(candidate, film_title, film_year, film_slug):
                            tmdb_id = candidate["id"]
                        else:
                            tmdb_id = None  # reject — better to lose the entry than insert a phantom

                    if not tmdb_id:
                        continue

                    movie = await movie_service.get_or_create_movie(
                        tmdb_id=tmdb_id,
                        letterboxd_uri=f"https://letterboxd.com/film/{film_slug}/"
                    )

                    if not movie:
                        continue

                    rating_stmt = select(UserRating).where(
                        UserRating.user_id == user_id,
                        UserRating.movie_id == movie.id
                    )
                    existing = (await db.execute(rating_stmt)).scalars().first()
                    if existing:
                        if not existing.is_watchlist:
                            existing.is_watchlist = True
                            watchlist_added += 1
                    else:
                        db.add(UserRating(user_id=user_id, movie_id=movie.id, is_watchlist=True))
                        watchlist_added += 1

            finally:
                await scraper.close()
                # tmdb is the injected singleton — never close it

            await db.commit()
            
            # Imp 10: Invalidate feed cache after sync completes
            await _invalidate_feed_cache(user_id)
            
            # Invalidate profile summary cache for LLM regeneration
            from services.profile_cache import invalidate_profile_summary
            from config import REDIS_URL
            await invalidate_profile_summary(user_id, REDIS_URL)
            
            logger.info(f"Background sync complete for user_id={user_id}. Watchlist added: {watchlist_added}")

        except Exception as e:
            logger.error(f"Background sync failed for user_id={user_id}: {e}")


@router.post("/sync/{username}", response_model=SyncResponse)
@limiter.limit("2/hour")
async def sync_user_data(
    username: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: TokenResponse = Depends(get_current_user)
):
# Fetch the authenticated user
    stmt = select(User).where(User.id == current_user.user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # L-3: require an explicit prior link before sync. Without this, a user with no
    # linked profile could trigger sync against any letterboxd handle.
    if not user.letterboxd_username:
        raise HTTPException(
            status_code=400,
            detail="Link your Letterboxd profile before syncing.",
        )
    if user.letterboxd_username != username:
        raise HTTPException(status_code=403, detail="Cannot sync another user's Letterboxd account")
    letterboxd_profile = user.letterboxd_username
    background_tasks.add_task(_run_sync_background, user.id, letterboxd_profile, tmdb)

    return {
        "status": "started",
        "stats": {},
        "message": f"Sync started for {letterboxd_profile}"
    }

@router.post("/group/vibe")
@limiter.limit("10/minute")
async def get_group_recommendations(
    http_request: Request,  # required by slowapi
    request: GroupVibeRequest,
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    current_user: TokenResponse = Depends(get_current_user)
):
    """
    Get recommendations based on the 'Group Vibe' (centroid of multiple users).
    H-2: Requesting user must be one of the group members.
    """
    # H-2: Ownership check — user must be in the group
    if current_user.username not in request.usernames:
        raise HTTPException(status_code=403, detail="Access denied: you must be a member of the group")
    rss_service = RSSService(db, tmdb=tmdb, qdrant=qdrant)
    
    # Get Hybrid Recommendations
    scored_results = await rss_service.get_group_recommendations_hybrid(request.usernames)
    
    if not scored_results:
        return []
        
    # Fetch full movie details
    tmdb_ids = [res['tmdb_id'] for res in scored_results]
    
    stmt = select(Movie).where(Movie.tmdb_id.in_(tmdb_ids))
    result = await db.execute(stmt)
    db_movies = result.scalars().all()
    
    # Map back to results to keep order/score
    movie_map = {m.tmdb_id: m for m in db_movies}

    # Inline ingest only for the small set of misses (cap to 5 to bound latency)
    missing = [res['tmdb_id'] for res in scored_results[:20] if res['tmdb_id'] not in movie_map][:5]
    if missing:
        from services.movie_service import MovieService
        movie_svc = MovieService(db, tmdb=tmdb)
        for tmdb_id in missing:
            try:
                m = await movie_svc.get_or_create_movie(tmdb_id)
                if m:
                    movie_map[tmdb_id] = m
            except Exception as e:
                logger.error(f"Group vibe movie ingest failed for tmdb_id={tmdb_id}: {e}")

    # Resolve providers for all movies in parallel
    import asyncio

    async def _build(res):
        movie = movie_map.get(res['tmdb_id'])
        if not movie:
            return None
        try:
            providers_data = await rss_service.tmdb.get_watch_providers(movie.tmdb_id, "ES")
        except Exception as e:
            logger.warning(f"Provider fetch failed for tmdb_id={movie.tmdb_id}: {e}")
            providers_data = None
        flat_providers = [p['provider_name'] for p in (providers_data or {}).get('flatrate', [])]
        return {
            "movie": movie,
            "similarity_score": res['score'],
            "providers": flat_providers,
            "contributors": [
                {"seed_title": c["username"], "contribution": c["score"]}
                for c in res.get("contributors", [])
            ]
        }

    tasks = [_build(res) for res in scored_results[:20]]
    final_results = [r for r in await asyncio.gather(*tasks) if r is not None]

    return final_results
