from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from typing import List, Dict
from pydantic import BaseModel, Field, conlist, constr
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
import os
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
#
# All four thresholds are env-tunable (F-34) so operators can dial recall vs
# precision without a redeploy. Defaults are the values validated against
# user 212's 552-row watchlist audit on 2026-05-15.
_FUZZY_TITLE_RATIO_STRICT = float(os.getenv("WATCHLIST_FUZZY_TITLE_STRICT", "0.95"))
_FUZZY_TITLE_RATIO_MIN = float(os.getenv("WATCHLIST_FUZZY_TITLE_MIN", "0.85"))
_FUZZY_VOTE_COUNT_MIN = int(os.getenv("WATCHLIST_FUZZY_VOTE_MIN", "20"))
_FUZZY_YEAR_TOLERANCE = int(os.getenv("WATCHLIST_FUZZY_YEAR_TOLERANCE", "1"))


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

# These are VectorBox usernames OR Letterboxd handles, so this is deliberately
# looser than LETTERBOXD_USERNAME_RE in users.py — VB usernames are seeded from
# an email prefix and legitimately contain '.', '+', '-'. What it DOES block is
# every URL-structural character ('/', '?', '#', '%', ':', '\', whitespace,
# control chars), because each value is interpolated into
# f"https://letterboxd.com/{username}/rss/" in rss_service.fetch_user_rss.
# The host is fixed before the injection point so this was never SSRF, but
# an unconstrained field aimed at a URL builder is a loaded gun.
_GROUP_HANDLE = constr(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._+\-]+$")


class GroupVibeRequest(BaseModel):
    usernames: conlist(_GROUP_HANDLE, min_length=2, max_length=8)
    # B-34: per-profile source override — {"username": "letterboxd"} forces the
    # RSS path even when a VectorBox account with that name exists. Anything
    # else (or absent) keeps the auto-detection (DB user preferred).
    sources: Optional[Dict[str, str]] = None
    # "tonight favours X": base scoring on this member's similarity instead of
    # the group max (None = balanced).
    focus: Optional[_GROUP_HANDLE] = None
    # Session filters ("we only have 90 min and filmin+hbo"): runtime cap in
    # minutes and/or provider names (case-insensitive match on TMDB names).
    max_runtime: Optional[int] = None
    providers: Optional[List[str]] = None
    # Los mismos dos controles que ya existen en el feed y en la watchlist: rango de
    # años y suelo de VBS. Acotados como allí — un año fuera de rango o un Q>100 no
    # es una petición, es un error, y vale más un 422 que una lista vacía sin motivo.
    year_min: Optional[int] = Field(None, ge=1800, le=2100)
    year_max: Optional[int] = Field(None, ge=1800, le=2100)
    min_score: Optional[float] = Field(None, ge=0, le=100)

async def _invalidate_feed_cache(user_id: int) -> None:
    """Delete all cached feed keys for this user after RSS sync or upload."""
    try:
        import redis.asyncio as aioredis
        import os

        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379")
        r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            from services.feed_service import FEED_CACHE_VERSION
            from services.cache_service import scan_and_delete
            deleted_count = 0
            for pattern in (
                f"section:{FEED_CACHE_VERSION}:{user_id}:*",
                f"signal_cache:{user_id}:*",
            ):
                deleted_count += await scan_and_delete(r, pattern)
            await r.delete(f"cluster_rotation:{FEED_CACHE_VERSION}:{user_id}")
            if deleted_count:
                logger.info(f"Invalidated {deleted_count} feed/signal cache keys and rotation for user_id={user_id}")
        finally:
            await r.close()
    except Exception as e:
        logger.error(f"Feed cache invalidation failed for user_id={user_id}: {e}")

def _watchlist_settled(last_lb_count: "int | None", lb_total: "int | None") -> bool:
    """May the scrape early-exit at the already-synced boundary, or must it run
    the full list for the removal reconcile?

    Letterboxd's own watchlist total (`data-num-entries`, free on page 1) vs the
    same number stored from the previous sync. Only ever LB-total vs LB-total, so
    our resolvable/irresolvable offset (series etc. we can't map to a TMDB movie)
    never enters the maths. Below the baseline → something left → full scrape.

    Decidable after page 1, which is the point: the answer cannot change later in
    the loop, so there is no mid-scrape flag to carry.

    ponytail: a removal MASKED by an equal addition keeps the total flat and reads
    as settled. That is the weekly `force_reconcile`'s job — it always was, for the
    irresolvable-masked case. Counting additions here to catch the resolvable half
    a few days earlier is what forced this gate to live at the boundary instead of
    at page 1; if same-sync masked-removal detection ever matters, the exit is to
    add `+ additions` back and re-introduce the flag.

    Degradation: no total (page-1 parse failed) → allow the stop (plain incremental,
    weekly net covers removals). No baseline (first sync / Redis-evicted) → full
    scrape, which reconciles and re-establishes the baseline.
    """
    if lb_total is None:
        return True
    return last_lb_count is not None and lb_total >= last_lb_count


def _scrape_was_complete(films_seen: int, lb_total: "int | None") -> bool:
    """Did the scrape actually reach the end of the list?

    `_scrape_listing_page` returns an empty page both at the real end of the list and
    on a 429/403/timeout the retries never cleared, and the loop breaks on either. So
    without this check a mid-list block looks like a finished full scrape, and the
    reconcile below demotes every row past the failure point. `films_seen` counts
    every film Letterboxd showed us, resolvable or not, so it is comparable to
    lb_total (measured live 2026-08-11: 603 == 603 over braisbg's 22 pages).

    `>=` not `==`: a film removed mid-scrape leaves us one short, and skipping a
    reconcile is free (the next sync runs it) while a wrong one loses data. No total
    → nothing to check against, allow it.
    """
    return lb_total is None or films_seen >= lb_total


async def _run_sync_background(
    user_id: int, letterboxd_profile: str, tmdb: TMDBClient, force_reconcile: bool = False
) -> None:
    """Background task — owns its own session. Never re-raises.

    Watchlist sync is INCREMENTAL by default: scrape newest-first page by page and
    early-exit at the first fully-already-synced page — but only if page 1's
    `data-num-entries` says the list hasn't shrunk since last sync
    (`_watchlist_settled`). If it has, we scrape the whole list so the F-31 reconcile
    can find what left. The weekly `force_reconcile` is the backstop for a removal
    masked by an equal addition, which keeps the total flat.
    """
    from config import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            rss_service = RSSService(db, tmdb=tmdb)
            await rss_service.sync_user_rss(letterboxd_profile, user_id)

            scraper = ScraperService()
            movie_service = MovieService(db, tmdb=tmdb)
            watchlist_added = 0

            # Removal-detection baseline (Redis): Letterboxd's own watchlist total
            # from the previous sync. Compared LB-total vs LB-total, so our
            # resolvable/irresolvable offset (series etc.) never enters the maths.
            import redis.asyncio as aioredis
            from config import REDIS_URL
            rds = aioredis.from_url(REDIS_URL, decode_responses=True)
            lb_total: "int | None" = None

            try:
                last_lb_count = None
                try:
                    _raw = await rds.get(f"rss:wl_lb_count:{user_id}")
                    last_lb_count = int(_raw) if _raw is not None else None
                except Exception:
                    last_lb_count = None

                # B-37 incremental watchlist sync: scrape newest-first PAGE BY PAGE
                # and STOP at the first page whose films are ALL already in this
                # user's watchlist (the "already-synced" boundary — the list is
                # date-added-descending, so new films only ever appear at the top)
                # — but only if _watchlist_settled cleared it off page 1's total.
                # Cuts a typical re-sync from ~22 pages to 1-2 and only resolves
                # NEW films.
                known_rows = await db.execute(
                    select(Movie.tmdb_id)
                    .join(UserRating, UserRating.movie_id == Movie.id)
                    .where(UserRating.user_id == user_id, UserRating.is_watchlist.is_(True))
                )
                known_tmdb_ids = {r[0] for r in known_rows.all()}

                # Resolved tmdb→movie ids seen this run (for the F-31 removal reconcile).
                resolved_movie_ids: set[int] = set()
                full_scrape = True  # False if we early-exit → skip the removal reconcile
                may_early_exit = False  # decided once, off page 1's total (see _watchlist_settled)
                MAX_WATCHLIST_PAGES = 50

                # Posición en la lista de Letterboxd, contada sobre TODO lo que la
                # página trae — también lo que no resuelve a TMDB (las series), o
                # las posiciones se desplazarían respecto a lo que el usuario ve.
                #
                # ponytail: un sync incremental sólo re-estampa las páginas que
                # visita. Tras añadir N películas, lo que queda por debajo del corte
                # arrastra su posición vieja (desfase N) hasta el siguiente scrape
                # completo, así que un par de filas pueden cruzarse justo en la
                # frontera. Se corrige solo; si algún día molesta, la salida es
                # sumar N a lo no visitado en vez de esperar al scrape completo.
                lb_rank = 0

                for page in range(1, MAX_WATCHLIST_PAGES + 1):
                    page_films, has_more, page_total = await scraper._scrape_listing_page(
                        letterboxd_profile, "watchlist", page
                    )
                    if page == 1:
                        # data-num-entries — Letterboxd's own watchlist total, page 1 only.
                        # It answers the early-exit question on its own, so decide here
                        # and carry a bool instead of re-asking at every boundary.
                        lb_total = page_total
                        may_early_exit = not force_reconcile and _watchlist_settled(last_lb_count, lb_total)
                        if not may_early_exit:
                            logger.info(
                                f"[watchlist-sync] user_id={user_id} full scrape "
                                f"({'forced' if force_reconcile else f'lb_total={lb_total} < baseline {last_lb_count}'})"
                            )
                    if not page_films:
                        break  # natural end (empty/404) — full_scrape stays True

                    page_tmdb_ids: list[int] = []
                    for item in page_films:
                        lb_rank += 1
                        film_slug = item["film_slug"]
                        film_year = item.get("year")
                        film_title = item.get("title")  # preserves accents/punct

                        tmdb_id = await scraper.get_tmdb_id(film_slug)  # slug cache (30d) → cheap on re-sync
                        if tmdb_id:
                            logger.info(
                                f"[watchlist-resolve] PAGE slug={film_slug!r} tmdb_id={tmdb_id} title={film_title!r}"
                            )
                        else:
                            # Fuzzy fallback — query with the richer scraped title when available.
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
                            tmdb_id = (
                                candidate["id"]
                                if candidate and _accept_fuzzy_match(candidate, film_title, film_year, film_slug)
                                else None
                            )
                            # Cache result (positive 30d / MISS 7d) so the next sync skips the search.
                            await scraper.set_resolved_tmdb_id(film_slug, tmdb_id)

                        if not tmdb_id:
                            continue
                        page_tmdb_ids.append(tmdb_id)

                        movie = await movie_service.get_or_create_movie(
                            tmdb_id=tmdb_id,
                            letterboxd_uri=f"https://letterboxd.com/film/{film_slug}/"
                        )
                        if not movie:
                            continue
                        resolved_movie_ids.add(movie.id)

                        rating_stmt = select(UserRating).where(
                            UserRating.user_id == user_id,
                            UserRating.movie_id == movie.id
                        )
                        existing = (await db.execute(rating_stmt)).scalars().first()
                        if existing:
                            existing.watchlist_rank = lb_rank
                            if not existing.is_watchlist:
                                existing.is_watchlist = True
                                watchlist_added += 1
                        else:
                            db.add(UserRating(
                                user_id=user_id, movie_id=movie.id,
                                is_watchlist=True, watchlist_rank=lb_rank,
                            ))
                            watchlist_added += 1

                    # Already-synced boundary: an entire page's films already in the
                    # watchlist. Page 1 already ruled out a removal, so stop here.
                    if may_early_exit and page_tmdb_ids and all(t in known_tmdb_ids for t in page_tmdb_ids):
                        full_scrape = False
                        logger.info(
                            f"[watchlist-sync] user_id={user_id} incremental stop at page {page}: "
                            f"all known, lb_total={lb_total} >= baseline {last_lb_count}"
                        )
                        break
                    if not has_more:
                        break  # natural end of the list

                # F-31 reconcile (removals) — ONLY on a scrape that saw the WHOLE
                # list, because it demotes everything it didn't see. Three ways it
                # didn't: an early-exit at the boundary; a scrape truncated by a
                # block the retries never cleared (`_scrape_was_complete` — the loop
                # cannot tell that from the end of the list); zero films resolved.
                # Skipping is always safe: the next sync reconciles.
                watchlist_removed = 0
                if not full_scrape:
                    logger.info(
                        f"[watchlist-reconcile] user_id={user_id} incremental sync (early-exit) — "
                        f"skipping removal reconcile (removals caught on the next full scrape)"
                    )
                elif not _scrape_was_complete(lb_rank, lb_total):
                    logger.warning(
                        f"[watchlist-reconcile] user_id={user_id} scrape came back SHORT "
                        f"({lb_rank}/{lb_total} films — blocked or rate-limited mid-list); "
                        f"skipping reconcile so the tail isn't demoted"
                    )
                elif resolved_movie_ids:
                    from sqlalchemy import update as sql_update
                    upd_result = await db.execute(
                        sql_update(UserRating)
                        .where(UserRating.user_id == user_id)
                        .where(UserRating.is_watchlist.is_(True))
                        .where(UserRating.movie_id.notin_(resolved_movie_ids))
                        .values(is_watchlist=False)
                    )
                    watchlist_removed = upd_result.rowcount or 0
                    if watchlist_removed:
                        logger.info(
                            f"[watchlist-reconcile] user_id={user_id} demoted {watchlist_removed} "
                            f"rows whose film was no longer in the Letterboxd watchlist"
                        )
                else:
                    logger.warning(
                        f"[watchlist-reconcile] user_id={user_id} resolved 0 films; "
                        f"skipping reconcile to avoid wiping on a transient scrape failure"
                    )

                # Store the fresh Letterboxd total as next sync's removal baseline.
                if lb_total is not None:
                    try:
                        await rds.set(f"rss:wl_lb_count:{user_id}", lb_total)
                    except Exception:
                        pass

            finally:
                await scraper.close()
                # Releases the lazily-created OMDb/Qdrant clients owned by this
                # task's MovieService. tmdb is the injected singleton — never
                # closed by MovieService.close() (it doesn't own it).
                await movie_service.close()
                try:
                    await rds.close()
                except Exception:
                    pass

            await db.commit()

            # F-37: keep onboarding_completed in sync after RSS ingest.
            try:
                from services.onboarding_service import maybe_complete_onboarding
                await maybe_complete_onboarding(user_id, db)
            except Exception as e:
                logger.warning(f"[onboarding] post-RSS flag refresh failed for user_id={user_id}: {e}")

            # Imp 10: Invalidate feed cache after sync completes
            await _invalidate_feed_cache(user_id)
            
            # Invalidate profile summary cache for LLM regeneration
            from services.profile_cache import invalidate_profile_summary
            from config import REDIS_URL
            await invalidate_profile_summary(user_id, REDIS_URL)
            
            logger.info(
                f"Background sync complete for user_id={user_id}. "
                f"Watchlist added: {watchlist_added}, demoted by reconcile: {watchlist_removed}"
            )

        except Exception as e:
            logger.error(f"Background sync failed for user_id={user_id}: {e}")
        finally:
            # Clear the in-progress flag so the UI stops spinning.
            try:
                import redis.asyncio as aioredis
                from config import REDIS_URL
                rr = aioredis.from_url(REDIS_URL, decode_responses=True)
                await rr.delete(f"rss:syncing:{user_id}")
                await rr.close()
            except Exception:
                pass


@router.get("/sync-status")
async def rss_sync_status(current_user: TokenResponse = Depends(get_current_user)):
    """Whether a background RSS sync is currently running for this user
    (drives the sidebar sync spinner)."""
    import redis.asyncio as aioredis
    from config import REDIS_URL
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        syncing = bool(await r.get(f"rss:syncing:{current_user.user_id}"))
    except Exception:
        syncing = False
    finally:
        try:
            await r.close()
        except Exception:
            pass
    return {"syncing": syncing}


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

    # Weekly SAFETY NET for removals: claim a once-per-week slot with an atomic
    # SET NX EX. Normally a removal is caught on the very next sync (see
    # _watchlist_settled: lb_total short of baseline+additions) even when an equal
    # addition keeps the raw total flat — this weekly forced full scrape is only
    # the backstop for the rare irresolvable-masked case.
    import redis.asyncio as aioredis
    from config import REDIS_URL
    force_reconcile = False
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        force_reconcile = bool(await r.set(f"rss:reconcile:{user.id}", "1", nx=True, ex=604800))
        # In-progress flag so the UI can spin until the sync actually finishes
        # (600s safety TTL — cleared by _run_sync_background's finally).
        await r.set(f"rss:syncing:{user.id}", "1", ex=600)
    except Exception:
        pass
    finally:
        try:
            await r.close()
        except Exception:
            pass

    background_tasks.add_task(_run_sync_background, user.id, letterboxd_profile, tmdb, force_reconcile)

    return {
        "status": "started",
        "stats": {},
        "message": f"Sync started for {letterboxd_profile}"
    }

@router.post("/group/vibe")
@limiter.limit("10/minute")
async def get_group_recommendations(
    # slowapi requires the starlette Request to be the param NAMED `request` —
    # naming the pydantic body `request` made the limiter wrapper 500 on every
    # call ("parameter `request` must be an instance of starlette.requests.Request").
    request: Request,
    payload: GroupVibeRequest,
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    current_user: TokenResponse = Depends(get_current_user)
):
    """
    Get recommendations based on the 'Group Vibe' (centroid of multiple users).

    H-2 RELAXED (user decision 2026-07-04): any signed-in user can run a group —
    Letterboxd profiles/watchlists are public anyway, and the old
    requester-must-be-a-member check just 403'd anyone typing a Letterboxd
    handle. Auth (Depends) + the 10/min rate limit stay.
    """
    rss_service = RSSService(db, tmdb=tmdb, qdrant=qdrant)

    # Get Hybrid Recommendations
    # Más candidatos SÓLO cuando hay filtros que puedan agotarlos. Sin filtros, los
    # 50 de siempre: el coste extra no compra nada y esta ruta ya es la más lenta.
    hay_filtros = bool(
        payload.max_runtime or payload.providers
        or payload.year_min or payload.year_max or payload.min_score is not None
    )
    scored_results = await rss_service.get_group_recommendations_hybrid(
        payload.usernames, sources=payload.sources, focus=payload.focus,
        limit=200 if hay_filtros else 50,
        # En ORIGEN, como las filas anchas del feed: la fusión excluye lo no elegible
        # antes de rankear, así que sus puestos se llenan con películas que pasan el
        # filtro. Los proveedores no viajan aquí porque no son un campo de la ficha
        # —hay que preguntarle a TMDB— y siguen siendo post-filtro, igual que en el feed.
        session_filters={
            "year_min": payload.year_min,
            "year_max": payload.year_max,
            "min_score": payload.min_score,
            "max_runtime": payload.max_runtime,
        } if hay_filtros else None,
    )
    
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

    # Member summaries (DB members get real counts; the rest are RSS guests).
    members = []
    db_member_ids: Dict[str, int] = {}
    for username in payload.usernames:
        # B-34: forced-letterboxd members are treated as RSS guests everywhere
        member = None
        if (payload.sources or {}).get(username) != "letterboxd":
            member = (
                await db.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
        if member:
            db_member_ids[username] = member.id
            films = (
                await db.execute(
                    select(func.count()).select_from(UserRating).where(
                        UserRating.user_id == member.id,
                        UserRating.rating.isnot(None),
                    )
                )
            ).scalar_one()
            members.append({"username": username, "source": "vectorbox", "films": int(films)})
        else:
            members.append({"username": username, "source": "letterboxd", "films": None})

    # Per-peer watchlist flags for the agreement matrix (DB members only —
    # guest watchlists aren't reachable via RSS).
    watchlisted_by: Dict[int, List[str]] = {}
    if db_member_ids and movie_map:
        movie_id_by_tmdb = {m.tmdb_id: m.id for m in movie_map.values()}
        rows = (
            await db.execute(
                select(UserRating.user_id, UserRating.movie_id).where(
                    UserRating.user_id.in_(list(db_member_ids.values())),
                    UserRating.movie_id.in_(list(movie_id_by_tmdb.values())),
                    UserRating.is_watchlist.is_(True),
                )
            )
        ).all()
        uid_to_name = {v: k for k, v in db_member_ids.items()}
        tmdb_by_movie_id = {v: k for k, v in movie_id_by_tmdb.items()}
        for uid, mid in rows:
            tid = tmdb_by_movie_id.get(mid)
            if tid is not None:
                watchlisted_by.setdefault(tid, []).append(uid_to_name[uid])

    # Resolve providers for all movies in parallel.
    # B-24 fix: serialize movies to plain dicts — the raw SQLAlchemy ORM object
    # in "movie" crashed FastAPI's jsonable_encoder (_sa_instance_state) → 500.
    import asyncio

    wanted_providers = {p.strip().lower() for p in (payload.providers or []) if p.strip()}

    def _passes_local(movie) -> bool:
        """Todo lo que se decide con la fila de Postgres. Ni una llamada de red.

        Va aparte de `_build` y ANTES que él porque `_build` pide proveedores a TMDB
        por película: con estos filtros dentro, ampliar el pool para que no se agote
        multiplicaba las llamadas a TMDB por diez. Filtrando primero por lo barato,
        el pool grande cuesta una consulta a Postgres y las llamadas caras se hacen
        sólo sobre las que ya han sobrevivido.
        """
        # No unreleased films in a "watch tonight together" list (user 2026-07-05).
        # is_upcoming alone missed in-production films with NO dates at all
        # (Merrily We Roll Along: year=None, runtime=0, is_upcoming=False) —
        # require a past-or-present year AND a real runtime.
        from datetime import date as _date
        if movie.is_upcoming or not movie.year or movie.year > _date.today().year or not movie.runtime:
            return False
        # Session runtime cap — unknown runtimes are dropped too ("we have 90
        # minutes" is a hard constraint, an unknown 3h film breaks the promise)
        if payload.max_runtime and movie.runtime > payload.max_runtime:
            return False
        # Rango de años. El año ya está garantizado no nulo por la guarda de arriba.
        if payload.year_min and movie.year < payload.year_min:
            return False
        if payload.year_max and movie.year > payload.year_max:
            return False
        # Suelo de calidad. Sin VBS NO pasa, al revés que el runtime desconocido:
        # "algo bueno" es una promesa que una película sin puntuar no puede cumplir,
        # y colarlas convertiría el filtro en ruido.
        if payload.min_score is not None and (
            movie.vectorbox_score is None or movie.vectorbox_score < payload.min_score
        ):
            return False
        return True

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
        if wanted_providers and not any(p.lower() in wanted_providers for p in flat_providers):
            return None
        return {
            "movie": {
                "tmdb_id": movie.tmdb_id,
                "title": movie.title,
                "year": movie.year,
                "runtime": movie.runtime,
                "genres": movie.genres or [],
                "overview": movie.overview,
                "poster_path": movie.poster_path,
                "vote_average": movie.vote_average,
                "vectorbox_score": movie.vectorbox_score,
                "title_es": movie.title_es,
            },
            "similarity_score": res['score'],
            "streaming_providers": flat_providers,
            "watchlisted_by": watchlisted_by.get(res['tmdb_id'], []),
            "contributors": [
                {"username": c["username"], "score": c["score"]}
                for c in res.get("contributors", [])
            ]
        }

    # Primero los filtros locales sobre TODO el pool, después las llamadas a TMDB
    # sólo sobre lo que sobrevivió. El orden es lo que hace asumible pedir 200
    # candidatos en vez de 50: la supervivencia de "2010+ y Q>=80 y menos de 90 min"
    # era 0 de 50 y pasa a haber material de verdad (medido con
    # scripts/audit_group_filters.py — un post-filtro sobre una búsqueda acotada se
    # queda sin nada EN SILENCIO, que es la trampa que ya costó un barrido aquí).
    supervivientes = [
        res for res in scored_results
        if res['tmdb_id'] in movie_map and _passes_local(movie_map[res['tmdb_id']])
    ]
    if len(supervivientes) < 20:
        logger.info(
            "[group] filtros locales dejan %d de %d candidatos",
            len(supervivientes), len(scored_results),
        )
    # El corte de proveedores sigue siendo un post-filtro con red, así que se le da
    # margen (50) sólo cuando está activo.
    pool = supervivientes[:50] if wanted_providers else supervivientes[:20]
    tasks = [_build(res) for res in pool]
    final_results = [r for r in await asyncio.gather(*tasks) if r is not None][:20]

    return {"members": members, "recommendations": final_results}
