from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from typing import List, Optional, Set, Dict
import random
import logging
import asyncio

from config import get_db, AsyncSessionLocal
from sqlalchemy.dialects.postgresql import insert as pg_insert
from models.database import UserRating, Movie, UserCluster
from models.schemas import (
    MovieMetadata,
    ClusterInfo,
    FeedResponse,
    FeedSection,
    FeedItem,
    FilteredSearchRequest
)
from services.clustering_service import ClusteringService
from services.tmdb_client import TMDBClient
from services.qdrant_service import QdrantService
from services.feed_service import FeedService
from services.provider_service import ProviderService
from dependencies import get_tmdb_client, get_qdrant_service, get_current_user, get_current_or_anonymous_user, get_embedding_service, get_redis
from limiter import limiter
from models.schemas import TokenResponse
from services.embedding_service import EmbeddingService

router = APIRouter(
    tags=["recommendations"]
)

logger = logging.getLogger(__name__)


async def _invalidate_user_feed_cache(user_id: int) -> None:
    """Sweep section, signal, and rotation cache keys for a user. Mirrors rss._invalidate_feed_cache.

    Matches the canonical pattern in rss.py so any web action that mutates a user's
    rating state (reject / watched) wipes the same surfaces an RSS sync would.
    """
    try:
        import os
        import redis.asyncio as aioredis
        from config import FEED_CACHE_VERSION
        from services.cache_service import scan_and_delete
        r = aioredis.from_url(
            os.environ.get("REDIS_URL", "redis://redis:6379"),
            decode_responses=True,
        )
        try:
            for pattern in (
                f"section:{FEED_CACHE_VERSION}:{user_id}:*",
                f"signal_cache:{user_id}:*",
            ):
                await scan_and_delete(r, pattern)
            await r.delete(f"cluster_rotation:{FEED_CACHE_VERSION}:{user_id}")
        finally:
            await r.close()
    except Exception as e:
        logger.warning(f"Feed cache invalidation failed for user_id={user_id}: {e}")

@router.post("/feed/filtered", response_model=FeedResponse)
@limiter.limit("20/minute")
async def filtered_feed(
    # slowapi needs the starlette Request named `request`.
    request: Request,
    payload: FilteredSearchRequest,
    # Anon-friendly (guest /explore rail), same as GET /feed.
    current_user: TokenResponse = Depends(get_current_or_anonymous_user),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding: EmbeddingService = Depends(get_embedding_service),
    redis=Depends(get_redis),
    background_tasks: BackgroundTasks = None,
):
    """F8: rail EXECUTE_QUERY as a SECTIONED feed. Same constraints as POST /filtered,
    but returns the full FeedResponse built from the WIDE rows only (Trident, Because
    You Watched, Hidden Gems) with the filter applied at each section's own search;
    the narrow rows (auteur/actor/niche/popular/wildcard/random/upcoming) are skipped.
    Providers are a post-filter (not a Qdrant payload field)."""
    qf: Dict = {}
    if payload.year_min:
        qf["year_min"] = payload.year_min
    if payload.year_max:
        qf["year_max"] = payload.year_max
    if payload.max_runtime:
        qf["max_runtime"] = payload.max_runtime
    if payload.genres:
        qf["include_genres"] = payload.genres
    if payload.min_score:
        qf["min_vectorbox_score"] = payload.min_score

    try:
        feed_service = FeedService(qdrant=qdrant, embedding_service=embedding)
        return await feed_service.get_main_feed(
            user_id=current_user.user_id,
            country_code=payload.country_code or "ES",
            streaming_providers=[],
            tmdb=tmdb,
            qdrant=qdrant,
            background_tasks=background_tasks,
            redis_client=redis,
            filters=qf or None,
            provider_filter=payload.providers or None,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Filtered feed generation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate filtered feed")


@router.get("/clusters/{user_id}", response_model=List[ClusterInfo])
async def get_user_clusters(
    user_id: int,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get user's taste clusters (moods)
    """
    # IDOR Check
    if current_user.user_id != user_id:
         raise HTTPException(status_code=403, detail="Access Denied")

    result = await db.execute(
        select(UserCluster).where(UserCluster.user_id == user_id)
    )
    clusters = result.scalars().all()
    
    if not clusters:
        raise HTTPException(
            status_code=404,
            detail="No clusters found. Please upload your Letterboxd data first."
        )
    
    # Build response with sample movies
    cluster_infos = []
    for cluster in clusters:
        # Get sample movies
        sample_movies = []
        if cluster.sample_movie_ids:
            result = await db.execute(
                select(Movie).where(Movie.id.in_(cluster.sample_movie_ids[:3]))
            )
            movies = result.scalars().all()
            
            for movie in movies:
                sample_movies.append(MovieMetadata(
                    tmdb_id=movie.tmdb_id,
                    title=movie.title,
                    original_title=movie.original_title,
                    year=movie.year,
                    runtime=movie.runtime,
                    genres=movie.genres or [],
                    overview=movie.overview,
                    poster_path=movie.poster_path,
                    backdrop_path=movie.backdrop_path,
                    vote_average=movie.vote_average
                ))
        
        cluster_infos.append(ClusterInfo(
            cluster_id=cluster.cluster_id,
            label=cluster.cluster_label,
            movie_count=cluster.movie_count,
            avg_rating=cluster.avg_rating,
            dominant_genres=cluster.dominant_genres or [],
            sample_movies=sample_movies
        ))
    
    return cluster_infos


@router.get("/why/{tmdb_id}")
@limiter.limit("30/minute")
async def why_this_film(
    request: Request,
    tmdb_id: int,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    qdrant: QdrantService = Depends(get_qdrant_service),
):
    """Recommendation-legibility breakdown for one film vs the current user.

    Feeds the ACID UI's why-this surfaces (rail D1 / dossier D2 / /why page D3):
      - trident:   {vibe, auteur, gems} presentation-layer split (sums to 1).
                   Heuristic over real signals — vibe = nearest-anchor cosine,
                   auteur = director overlap with the user's 4★+ films,
                   gems = quality x low-popularity. NOT the engine's internal
                   RRF weights (those aren't persisted per film).
      - anchors:   the user's rated films most similar to this one (weights
                   normalized over the top 3).
      - neighbors: nearest films in the user's rated library by cosine distance.
      - cluster:   the user's taste cluster whose medoid is nearest this film.
      - rank/rank_pool: null — no persisted per-day ranked pool yet (UI hides).
    """
    import numpy as np

    user_id = current_user.user_id

    movie = (
        await db.execute(select(Movie).where(Movie.tmdb_id == tmdb_id))
    ).scalar_one_or_none()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not in catalogue")

    # Stored catalogue vector only — regenerating on the fly creates an
    # asymmetric vector space (see CLAUDE.md embedding hygiene).
    target_vec = await qdrant.get_vector(tmdb_id)

    # User's rated library (most recent 300 caps the vector fetch).
    rated_rows = (
        await db.execute(
            select(UserRating, Movie)
            .join(Movie, Movie.id == UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                UserRating.rating.isnot(None),
                Movie.tmdb_id != tmdb_id,
            )
            .order_by(desc(UserRating.created_at))
            .limit(300)
        )
    ).all()

    neighbors: list[dict] = []
    anchors: list[dict] = []
    max_sim = 0.0

    if target_vec is not None and rated_rows:
        rated_tmdb_ids = [m.tmdb_id for _, m in rated_rows]
        vec_map = await qdrant.get_vectors_batch(rated_tmdb_ids)

        scored = []  # (sim, rating_row, movie_row)
        t = np.asarray(target_vec, dtype=np.float32)
        t_norm = np.linalg.norm(t) or 1.0
        for rating_row, movie_row in rated_rows:
            v = vec_map.get(movie_row.tmdb_id)
            if v is None:
                continue
            v = np.asarray(v, dtype=np.float32)
            sim = float(np.dot(t, v) / (t_norm * (np.linalg.norm(v) or 1.0)))
            scored.append((sim, rating_row, movie_row))
        scored.sort(key=lambda s: s[0], reverse=True)

        if scored:
            max_sim = max(0.0, scored[0][0])

        neighbors = [
            {
                "tmdb_id": m.tmdb_id,
                "title": m.title,
                "year": m.year,
                "dist": round(max(0.0, 1.0 - sim), 2),
                "poster_url": m.poster_path,
            }
            for sim, _, m in scored[:5]
        ]

        anchor_pool = [(sim, r, m) for sim, r, m in scored if (r.rating or 0) >= 3.5][:3]
        weight_total = sum(max(s, 0.0) for s, _, _ in anchor_pool) or 1.0
        anchors = [
            {
                "tmdb_id": m.tmdb_id,
                "title": m.title,
                "year": m.year,
                "rating": r.rating,
                "weight": round(max(sim, 0.0) / weight_total, 2),
                "reason": (
                    f"rewatched {r.watch_count}x" if (r.watch_count or 0) > 1
                    else f"rated {r.rating:g}★"
                    + (f" · {r.watched_date.strftime('%b %Y').lower()}" if r.watched_date else "")
                ),
                "poster_url": m.poster_path,
            }
            for sim, r, m in anchor_pool
        ]

    # Cluster: nearest medoid among the user's taste clusters.
    cluster_out = None
    clusters = (
        await db.execute(select(UserCluster).where(UserCluster.user_id == user_id))
    ).scalars().all()
    if target_vec is not None and clusters:
        medoid_ids = [c.medoid_movie_id for c in clusters if c.medoid_movie_id]
        if medoid_ids:
            medoid_movies = (
                await db.execute(select(Movie).where(Movie.id.in_(medoid_ids)))
            ).scalars().all()
            medoid_tmdb = {m.id: m.tmdb_id for m in medoid_movies}
            medoid_vecs = await qdrant.get_vectors_batch(list(medoid_tmdb.values()))
            t = np.asarray(target_vec, dtype=np.float32)
            t_norm = np.linalg.norm(t) or 1.0
            best = None
            for c in clusters:
                mv = medoid_vecs.get(medoid_tmdb.get(c.medoid_movie_id))
                if mv is None:
                    continue
                v = np.asarray(mv, dtype=np.float32)
                sim = float(np.dot(t, v) / (t_norm * (np.linalg.norm(v) or 1.0)))
                if best is None or sim > best[0]:
                    best = (sim, c)
            if best:
                c = best[1]
                cluster_out = {
                    "id": c.cluster_id,
                    "name": c.cluster_label,
                    "movie_count": c.movie_count,
                    "avg_rating": c.avg_rating,
                }

    # Auteur signal: does this film's director appear in the user's 4★+ films?
    auteur_out = None
    auteur_matches = 0
    director = (movie.directors or [None])[0]
    if director and rated_rows:
        auteur_matches = sum(
            1
            for r, m in rated_rows
            if (r.rating or 0) >= 4.0 and director in (m.directors or [])
        )
        if auteur_matches:
            auteur_out = {
                "name": director,
                "films_rated": auteur_matches,
                "note": f"{auteur_matches} film{'s' if auteur_matches != 1 else ''} of theirs rated 4★+",
            }

    # Gem signal: high quality x low visibility.
    votes = movie.imdb_vote_count or movie.vote_count or 0
    quality = (movie.vectorbox_score or 50.0) / 100.0
    obscurity = 1.0 if votes < 5000 else 0.6 if votes < 20000 else 0.3 if votes < 100000 else 0.1
    gem_out = (
        {"vote_count": votes, "note": f"under-watched · Q{round((movie.vectorbox_score or 0)):d}"}
        if obscurity >= 0.6 and quality >= 0.7
        else None
    )

    # Trident: normalize the three raw signals into a presentation split.
    vibe_raw = max(max_sim, 0.05)
    auteur_raw = min(1.0, 0.45 * auteur_matches)
    gems_raw = quality * obscurity
    total = vibe_raw + auteur_raw + gems_raw
    trident = {
        "vibe": round(vibe_raw / total, 2),
        "auteur": round(auteur_raw / total, 2),
        "gems": round(gems_raw / total, 2),
    }

    # Rank within the live picked_for_you pool (TTL-bounded; None on cache miss).
    from services.feed_service import get_cached_rank
    rank_info = await get_cached_rank(user_id, tmdb_id)

    return {
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "year": movie.year,
        "runtime": movie.runtime,
        "director": director,
        "q": movie.vectorbox_score,
        "poster_url": movie.poster_path,
        "trident": trident,
        "anchors": anchors,
        "neighbors": neighbors,
        "cluster": cluster_out,
        "auteur": auteur_out,
        "gem": gem_out,
        "rank": rank_info[0] if rank_info else None,
        "rank_pool": rank_info[1] if rank_info else None,
    }


@router.get("/space")
@limiter.limit("10/minute")
async def vector_space(
    request: Request,
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    qdrant: QdrantService = Depends(get_qdrant_service),
):
    """2-D projection of the user's taste map for the ACID /space canvas.

    - points: rated films (seen=true) + watchlist films (seen=false), each with
      normalized [0,1] coords, cluster_id (nearest user-cluster medoid), Q and
      `dc` = cosine distance to the user's taste centroid in the full 768-d
      space (drives the "nearest to your centroid" drawer).
    - centroid: the YOU marker, projected through the same PCA basis.
    - PCA via numpy SVD: deterministic (sign-fixed), no extra deps.
      ponytail: linear PCA is the floor; swap to UMAP only if real users say
      the layout reads poorly.
    """
    import numpy as np

    user_id = current_user.user_id

    # Cap rated and watchlist separately — rated films are the substance of
    # the map ("seen"), watchlist is context ("unseen"). A single recency cap
    # let a freshly-imported watchlist crowd out the rated library.
    rated_q = (
        select(UserRating, Movie)
        .join(Movie, Movie.id == UserRating.movie_id)
        .where(
            UserRating.user_id == user_id,
            UserRating.rating.isnot(None),
            UserRating.is_rejected.is_(False),
        )
        .order_by(desc(UserRating.created_at))
        .limit(450)
    )
    watch_q = (
        select(UserRating, Movie)
        .join(Movie, Movie.id == UserRating.movie_id)
        .where(
            UserRating.user_id == user_id,
            UserRating.rating.is_(None),
            UserRating.is_watchlist.is_(True),
            UserRating.is_rejected.is_(False),
        )
        .order_by(desc(UserRating.created_at))
        .limit(150)
    )
    rows = list((await db.execute(rated_q)).all()) + list((await db.execute(watch_q)).all())
    if len(rows) < 3:
        return {"points": [], "centroid": None, "clusters": [], "total": 0}

    vec_map = await qdrant.get_vectors_batch([m.tmdb_id for _, m in rows])
    placed = [(r, m, vec_map[m.tmdb_id]) for r, m in rows if m.tmdb_id in vec_map]
    if len(placed) < 3:
        return {"points": [], "centroid": None, "clusters": [], "total": 0}

    clusters = (
        await db.execute(select(UserCluster).where(UserCluster.user_id == user_id))
    ).scalars().all()
    medoid_ids = [c.medoid_movie_id for c in clusters if c.medoid_movie_id]
    medoid_tmdb: Dict[int, int] = {}
    if medoid_ids:
        medoid_movies = (
            await db.execute(select(Movie).where(Movie.id.in_(medoid_ids)))
        ).scalars().all()
        medoid_tmdb = {m.id: m.tmdb_id for m in medoid_movies}
    medoid_vecs = await qdrant.get_vectors_batch(list(medoid_tmdb.values())) if medoid_tmdb else {}

    def _project():
        X = np.asarray([v for _, _, v in placed], dtype=np.float32)
        # Rated-only centroid = the taste center (watchlist shouldn't drag YOU).
        rated_mask = np.asarray([(r.rating is not None) for r, _, _ in placed])
        center_of = X[rated_mask] if rated_mask.any() else X
        centroid_vec = center_of.mean(axis=0)

        mean = X.mean(axis=0)
        Xc = X - mean
        # SVD → top-2 principal axes; fix sign so layout is stable across reloads.
        _, _, Vt = np.linalg.svd(Xc, full_matrices=False)
        basis = Vt[:2]
        for i in range(2):
            if basis[i][np.argmax(np.abs(basis[i]))] < 0:
                basis[i] = -basis[i]
        coords = Xc @ basis.T                      # (n, 2)
        c_xy = (centroid_vec - mean) @ basis.T     # (2,)

        # Normalize everything into [0,1] with a small margin.
        all_xy = np.vstack([coords, c_xy])
        lo, hi = all_xy.min(axis=0), all_xy.max(axis=0)
        span = np.where((hi - lo) > 1e-6, hi - lo, 1.0)
        norm = lambda p: ((p - lo) / span * 0.9 + 0.05)  # noqa: E731
        coords_n = norm(coords)
        c_n = norm(c_xy)

        # Cosine distance to centroid in full 768-d.
        cn = centroid_vec / (np.linalg.norm(centroid_vec) or 1.0)
        Xn = X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
        dc = 1.0 - Xn @ cn

        # Cluster per point via nearest medoid (cosine).
        cluster_ids = [None] * len(placed)
        if medoid_vecs:
            order = [c for c in clusters if medoid_tmdb.get(c.medoid_movie_id) in medoid_vecs]
            M = np.asarray([medoid_vecs[medoid_tmdb[c.medoid_movie_id]] for c in order], dtype=np.float32)
            Mn = M / np.maximum(np.linalg.norm(M, axis=1, keepdims=True), 1e-9)
            sims = Xn @ Mn.T                      # (n, k)
            nearest = sims.argmax(axis=1)
            cluster_ids = [order[j].cluster_id for j in nearest]

        return coords_n, c_n, dc, cluster_ids

    loop = asyncio.get_running_loop()
    coords_n, c_n, dc, cluster_ids = await loop.run_in_executor(None, _project)

    points = [
        {
            "tmdb_id": m.tmdb_id,
            "title": m.title,
            "year": m.year,
            "poster_url": m.poster_path,
            "x": round(float(coords_n[i][0]), 4),
            "y": round(float(coords_n[i][1]), 4),
            "cluster_id": cluster_ids[i],
            "seen": r.rating is not None,
            "q": m.vectorbox_score,
            "dc": round(float(dc[i]), 3),
        }
        for i, (r, m, _) in enumerate(placed)
    ]
    return {
        "points": points,
        "centroid": {"x": round(float(c_n[0]), 4), "y": round(float(c_n[1]), 4)},
        "clusters": [{"id": c.cluster_id, "name": c.cluster_label} for c in clusters],
        "total": len(points),
    }


@router.get("/feed", response_model=FeedResponse)
@limiter.limit("20/minute")
async def get_feed(
    request: Request,
    current_user: TokenResponse = Depends(get_current_or_anonymous_user),
    scope: str = "global",
    country_code: str = "ES",
    streaming_providers: str = "",
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding: EmbeddingService = Depends(get_embedding_service),
    redis=Depends(get_redis),
    background_tasks: BackgroundTasks = None
):
    """
    Get Netflix-style multi-strategy recommendation feed.
    """
    user_id = current_user.user_id

    try:
        provider_ids = []
        if streaming_providers:
            provider_ids = [int(x) for x in streaming_providers.split(",") if x.strip()]
        
        # Decision (2026-05-17): users with sub-clustering ratings (e.g. just
        # migrated from a guest with 3 rated films, or a fresh ZIP that's
        # still enriching in background) should NOT see "data incomplete".
        # The feed pipeline already gracefully degrades — personalized
        # sections (BYW, Picked For You, Cult Actor) return None when their
        # input signals are too sparse, and we filter those out client-side.
        # Non-personalized sections (Hidden Gems, Niche Picks, Upcoming,
        # Random Picks, Popular on Letterboxd) work fine without clusters.
        # Old "data incomplete" gate forced a ZIP/onboarding wall on every
        # sub-threshold user — same friction as the guest cap we already
        # removed.

        # Services are now injected
        feed_service = FeedService(qdrant=qdrant, embedding_service=embedding)
        
        if scope == "watchlist":
            return await feed_service.get_watchlist_feed(user_id, db, tmdb, country_code, provider_ids)
        
        # Delegate parallel execution to the service
        return await feed_service.get_main_feed(
            user_id=user_id,
            country_code=country_code,
            streaming_providers=provider_ids,
            tmdb=tmdb,
            qdrant=qdrant,
            background_tasks=background_tasks,
            redis_client=redis,
        )
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Feed generation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate feed")


@router.get("/watchlist")
@limiter.limit("20/minute")
async def get_watchlist(
    request: Request,
    current_user: TokenResponse = Depends(get_current_user),
    page: int = 1,
    limit: int = 20,
    country_code: str = "ES",
    sort_by: str = "date_added",
    runtime_min: Optional[int] = None,
    runtime_max: Optional[int] = None,
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    genres: Optional[str] = None,
    min_rating: Optional[float] = None,
    streaming_providers: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding: EmbeddingService = Depends(get_embedding_service)
):
    """
    Get filtered watchlist items for grid view.
    """
    user_id = current_user.user_id
    
    feed_service = FeedService(qdrant=qdrant, embedding_service=embedding)

    stmt = (
        select(Movie)
        .join(UserRating, Movie.id == UserRating.movie_id)
        .where(
            UserRating.user_id == user_id,
            UserRating.is_watchlist.is_(True),
            UserRating.is_watched.is_(False)
        )
    )

    # Push scalar filters to DB
    if runtime_min: stmt = stmt.where(Movie.runtime >= runtime_min)
    if runtime_max: stmt = stmt.where(Movie.runtime <= runtime_max)
    if year_min: stmt = stmt.where(Movie.year >= year_min)
    if year_max: stmt = stmt.where(Movie.year <= year_max)
    if min_rating: stmt = stmt.where(Movie.vectorbox_score >= min_rating)

    # Push genre filter to DB using PostgreSQL array overlap
    if genres:
        genre_list = [g.strip() for g in genres.split(",")]
        stmt = stmt.where(Movie.genres.overlap(genre_list))

    # Push sort to DB
    if sort_by == "date_added":
        stmt = stmt.order_by(Movie.id.desc())
    elif sort_by == "title":
        stmt = stmt.order_by(Movie.title)
    elif sort_by == "rating":
        stmt = stmt.order_by(Movie.vectorbox_score.desc().nulls_last())

    final_items = []
    provider_service = ProviderService(db, tmdb)

    provider_ids = []
    if streaming_providers:
        provider_ids = [int(x) for x in streaming_providers.split(",") if x.strip()]

    if provider_ids:
        # Streaming path: load bounded candidates, filter by provider, paginate in Python
        result = await db.execute(stmt.limit(500))
        candidates = result.scalars().all()

        providers_map = await provider_service.get_providers_batch([m.id for m in candidates], country_code)

        available_movies = []
        for movie in candidates:
            movie_providers = providers_map.get(movie.id, [])
            if any(p["provider_id"] in provider_ids for p in movie_providers):
                flat_providers = [p["provider_name"] for p in movie_providers]
                available_movies.append((movie, flat_providers))

        total_items = len(available_movies)
        start = (page - 1) * limit
        paginated = available_movies[start:start + limit]

        for movie, providers in paginated:
            item = await feed_service.engine.create_feed_item(movie, 1.0, country_code, tmdb, streaming_providers=providers)
            final_items.append(item)

    else:
        # No streaming filter: count + DB-level pagination
        count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
        total_items = count_result.scalar_one()

        paginated_stmt = stmt.limit(limit).offset((page - 1) * limit)
        result = await db.execute(paginated_stmt)
        paginated_movies = result.scalars().all()

        movie_ids = [m.id for m in paginated_movies]
        providers_map = await provider_service.get_providers_batch(movie_ids, country_code)

        for movie in paginated_movies:
            movie_providers = providers_map.get(movie.id, [])
            flat_providers = [p["provider_name"] for p in movie_providers]
            item = await feed_service.engine.create_feed_item(movie, 1.0, country_code, tmdb, streaming_providers=flat_providers)
            final_items.append(item)

    # Whole-queue aggregates for the ACID hero strip (independent of filters).
    stats_row = (
        await db.execute(
            select(
                func.coalesce(func.sum(Movie.runtime), 0),
                func.count(),
                func.count().filter(Movie.is_upcoming.is_(True)),
            )
            .select_from(Movie)
            .join(UserRating, Movie.id == UserRating.movie_id)
            .where(
                UserRating.user_id == user_id,
                UserRating.is_watchlist.is_(True),
                UserRating.is_watched.is_(False),
            )
        )
    ).first()
    stats = {
        "total_runtime_min": int(stats_row[0] or 0),
        "total_films": int(stats_row[1] or 0),
        "upcoming": int(stats_row[2] or 0),
    }

    return {"items": final_items, "total": total_items, "page": page, "limit": limit, "stats": stats}



@router.get("/random-row", response_model=FeedSection)
@limiter.limit("20/minute")
async def get_random_row(
    request: Request,
    current_user: TokenResponse = Depends(get_current_user),
    country_code: str = "ES",
    scope: str = "global",
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding: EmbeddingService = Depends(get_embedding_service)
):
    """
    Get a fresh set of random recommendations (Reroll functionality).
    """
    user_id = current_user.user_id
    
    feed_service = FeedService(qdrant=qdrant, embedding_service=embedding)
    try:
        if scope == "watchlist":
            stmt = (
                select(Movie)
                .join(UserRating, Movie.id == UserRating.movie_id)
                .where(
                    UserRating.user_id == user_id,
                    UserRating.is_watchlist.is_(True),
                    UserRating.is_watched.is_(False)
                )
            )
            result = await db.execute(stmt)
            watchlist_movies = result.scalars().all()
            
            if not watchlist_movies:
                raise HTTPException(status_code=404, detail="Watchlist empty")
                
            random_movies = list(watchlist_movies)
            random.shuffle(random_movies)
            
            selected_movies = random_movies[:10]
            
            # Batch fetch providers
            movie_ids = [m.id for m in selected_movies]
            provider_service = ProviderService(db, tmdb)
            providers_map = await provider_service.get_providers_batch(movie_ids, country_code)
            
            items = []
            for movie in selected_movies:
                 providers_data = providers_map.get(movie.id, [])
                 provider_names = [p["provider_name"] for p in providers_data]
                 
                 item = await feed_service.engine.create_feed_item(
                     movie=movie, 
                     score=0.85, 
                     country=country_code, 
                     tmdb=tmdb,
                     streaming_providers=provider_names
                 )
                 items.append(item)
                 
            return FeedSection(
                id="random_watchlist",
                title="Shuffle: From Your Watchlist",
                type="watchlist_random",
                items=items
            )
        else:
            section = await feed_service.get_random_recommendations_section(user_id, db, tmdb, set(), country_code)
            if not section:
                raise HTTPException(status_code=404, detail="Could not generate random recommendations")
            return section
    except Exception as e:
        logger.error(f"Random row failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate random recommendations")


@router.get("/hidden-gems", response_model=FeedSection)
@limiter.limit("20/minute")
async def get_hidden_gems_row(
    request: Request,
    current_user: TokenResponse = Depends(get_current_user),
    country_code: str = "ES",
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedding: EmbeddingService = Depends(get_embedding_service)
):
    """
    Get a fresh set of hidden gems (Reroll functionality).
    """
    user_id = current_user.user_id
    feed_service = FeedService(qdrant=qdrant, embedding_service=embedding)
    try:
        from sqlalchemy import or_
        from services.feed_service import get_cached_feed_tmdb_ids
        from services.recommendation_engine import MOVIE_QUALITY_GATE, _get_signal_c_thresholds

        # 1. Same dynamic quality bar as the feed's hidden_gems row (rich
        #    profiles => VBS >= 70) — a reroll must never LOWER the bar.
        user_movie_count = (
            await db.execute(
                select(func.count(UserRating.id))
                .where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
            )
        ).scalar() or 0
        thresholds = _get_signal_c_thresholds(user_movie_count)

        excluded_result = await db.execute(
            select(UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .where(or_(UserRating.is_watched.is_(True), UserRating.is_rejected.is_(True)))
        )
        excluded_internal_ids = set(excluded_result.scalars().all())

        # 2. A reroll must bring NEW films: exclude everything the user's
        #    current (cached) feed is already showing, across ALL sections.
        feed_tmdb_ids = await get_cached_feed_tmdb_ids(user_id)

        pool_stmt = (
            select(Movie)
            .where(*MOVIE_QUALITY_GATE)
            .where(Movie.has_enriched_embedding.is_(True))
            .where(Movie.vectorbox_score >= thresholds["min_score"])
            .where(Movie.popularity <= thresholds["max_popularity"])
            .where(Movie.vote_count >= thresholds["min_votes"])
            .where(Movie.id.notin_(excluded_internal_ids) if excluded_internal_ids else True)
            .where(Movie.tmdb_id.notin_(feed_tmdb_ids) if feed_tmdb_ids else True)
            .order_by(desc(Movie.vectorbox_score))
            .limit(200)
        )
        pool = (await db.execute(pool_stmt)).scalars().all()
        if not pool:
            raise HTTPException(status_code=404, detail="No hidden gems found")

        # 3. Random sample = variety on every reroll; the pool is already
        #    quality-gated, so any sample is a valid gems row.
        valid_movies = random.sample(pool, min(10, len(pool)))

        # Batch fetch providers
        valid_ids = [m.id for m in valid_movies]
        provider_service = ProviderService(db, tmdb)
        providers_map = await provider_service.get_providers_batch(valid_ids, country_code)

        items = []
        for movie in valid_movies:
            providers_data = providers_map.get(movie.id, [])
            provider_names = [p["provider_name"] for p in providers_data]

            item = await feed_service.engine.create_feed_item(
                movie=movie,
                score=(movie.vectorbox_score or 0) / 100.0,
                country=country_code,
                tmdb=tmdb,
                streaming_providers=provider_names
            )
            items.append(item)

        return FeedSection(
            id="hidden_gems",
            title="Hidden Gems",
            items=items
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Hidden gems failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate hidden gems")


@router.post("/reject/{tmdb_id}")
@limiter.limit("60/minute")
async def reject_movie(
    request: Request,
    tmdb_id: int,
    background_tasks: BackgroundTasks,
    current_user: TokenResponse = Depends(get_current_or_anonymous_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a movie as 'Not Interested'. Upserts UserRating with is_rejected=True.

    Cache invalidation runs as a background task — same pattern as
    mark_watched: keep the response fast so rapid-fire clicks don't
    stack the SCAN+DELETE inside the request path.
    """
    user_id = current_user.user_id

    # Find the internal movie by tmdb_id
    movie_result = await db.execute(
        select(Movie).where(Movie.tmdb_id == tmdb_id)
    )
    movie = movie_result.scalar_one_or_none()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    # CONC-1 parity with /onboarding/rate: atomic INSERT … ON CONFLICT so two
    # concurrent rejects (double-click) can't both pass a "not exists" check
    # and collide on the user/movie unique index → 500. On conflict only
    # is_rejected flips; the rest of the row is preserved.
    await db.execute(
        pg_insert(UserRating)
        .values(
            user_id=user_id,
            movie_id=movie.id,
            is_rejected=True,
            is_watched=False,
        )
        .on_conflict_do_update(
            index_elements=[UserRating.user_id, UserRating.movie_id],
            set_={"is_rejected": True},
        )
    )
    await db.commit()

    background_tasks.add_task(_invalidate_user_feed_cache, user_id)

    return {"status": "ok", "tmdb_id": tmdb_id, "rejected": True}


@router.get("/movies/rejected")
async def get_rejected_movies(
    current_user: TokenResponse = Depends(get_current_or_anonymous_user),
    db: AsyncSession = Depends(get_db),
):
    """List all movies the user has marked as 'Not Interested'."""
    user_id = current_user.user_id

    result = await db.execute(
        select(Movie.tmdb_id, Movie.title, Movie.year, Movie.poster_path)
        .join(UserRating, Movie.id == UserRating.movie_id)
        .where(
            UserRating.user_id == user_id,
            UserRating.is_rejected.is_(True),
        )
        .order_by(UserRating.created_at.desc())
    )
    rows = result.all()

    return [
        {
            "tmdb_id": row.tmdb_id,
            "title": row.title,
            "year": row.year,
            "poster_path": row.poster_path,
        }
        for row in rows
    ]


@router.delete("/movies/{tmdb_id}/reject")
@limiter.limit("60/minute")
async def unreject_movie(
    request: Request,
    tmdb_id: int,
    current_user: TokenResponse = Depends(get_current_or_anonymous_user),
    db: AsyncSession = Depends(get_db),
):
    """Undo a 'Not Interested' rejection."""
    user_id = current_user.user_id

    movie_result = await db.execute(
        select(Movie).where(Movie.tmdb_id == tmdb_id)
    )
    movie = movie_result.scalar_one_or_none()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    existing_result = await db.execute(
        select(UserRating).where(
            UserRating.user_id == user_id,
            UserRating.movie_id == movie.id,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if not existing or not existing.is_rejected:
        raise HTTPException(status_code=404, detail="Movie is not rejected")

    existing.is_rejected = False
    await db.commit()

    await _invalidate_user_feed_cache(user_id)

    return {"status": "ok", "tmdb_id": tmdb_id, "rejected": False}


@router.post("/movies/{tmdb_id}/watched")
@limiter.limit("60/minute")
async def mark_watched(
    request: Request,
    tmdb_id: int,
    background_tasks: BackgroundTasks,
    current_user: TokenResponse = Depends(get_current_or_anonymous_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a movie as watched from the web (no date or rewatch info available).

    Cache invalidation runs as a background task so the response returns
    immediately. Otherwise rapid-fire clicks ("watched 3 films in a row")
    were stacking the SCAN+DELETE inside the request path; each next click
    waited for the previous one to finish, and the feed refetch the
    frontend triggered after each click could land before the next commit
    propagated → user saw the just-watched film reappear in the feed.
    """
    user_id = current_user.user_id

    movie_result = await db.execute(
        select(Movie).where(Movie.tmdb_id == tmdb_id)
    )
    movie = movie_result.scalar_one_or_none()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    # CONC-1 parity with /onboarding/rate: atomic upsert. On conflict only
    # is_watched flips — an existing row keeps its real watch_count; the
    # insert path uses watch_count=0 ("marked from web" sentinel, see
    # _web_watches_query).
    await db.execute(
        pg_insert(UserRating)
        .values(
            user_id=user_id,
            movie_id=movie.id,
            is_watched=True,
            watch_count=0,
        )
        .on_conflict_do_update(
            index_elements=[UserRating.user_id, UserRating.movie_id],
            set_={"is_watched": True},
        )
    )
    await db.commit()

    background_tasks.add_task(_invalidate_user_feed_cache, user_id)

    return {"status": "ok", "tmdb_id": tmdb_id, "watched": True}


def _web_watches_query(user_id: int, *, ascending: bool):
    """Films the user marked watched on VectorBox (sentinel: `is_watched=true
    AND watch_count=0`) — set by `mark_watched` when the source is the web,
    not a Letterboxd ZIP import."""
    order = UserRating.created_at.asc() if ascending else UserRating.created_at.desc()
    return (
        select(
            Movie.tmdb_id, Movie.title, Movie.year,
            Movie.letterboxd_uri, Movie.poster_path,
            UserRating.created_at,
        )
        .join(UserRating, Movie.id == UserRating.movie_id)
        .where(UserRating.user_id == user_id)
        .where(UserRating.is_watched.is_(True))
        .where(UserRating.watch_count == 0)
        .order_by(order)
    )


@router.get("/movies/watched-on-web")
async def list_web_watches(
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """F-22: list films the user marked as watched directly on VectorBox.
    These never made it to Letterboxd because we can't write there; the UI
    uses this for manual reconciliation or to trigger the CSV export."""
    rows = (await db.execute(_web_watches_query(current_user.user_id, ascending=False))).all()
    return [
        {
            "tmdb_id": r.tmdb_id,
            "title": r.title,
            "year": r.year,
            "letterboxd_uri": r.letterboxd_uri,
            "poster_path": r.poster_path,
            "watched_date": r.created_at.date().isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def _csv_safe(value) -> str:
    """Defuse CSV/spreadsheet formula injection.

    Excel/Numbers/LibreOffice treat any cell starting with `=`, `+`, `-`,
    `@`, `\t`, or `\r` as a formula. A movie title like
    `=HYPERLINK("https://evil","ok")` would execute on open. Prefix the
    cell with a single quote — Excel renders it as text, never as a formula.
    """
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


@router.get("/movies/watched-on-web.csv")
async def export_web_watches_csv(
    current_user: TokenResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """F-22: Letterboxd-import-compatible CSV of the user's web-marked
    watches. Columns: Letterboxd URI (preferred), Title, Year, WatchedDate.
    See https://letterboxd.com/about/importing-data/. Title+Year is the
    fallback match when URI is missing."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    user_id = current_user.user_id
    rows = (await db.execute(_web_watches_query(user_id, ascending=True))).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Letterboxd URI", "Title", "Year", "WatchedDate"])
    for r in rows:
        writer.writerow([
            _csv_safe(r.letterboxd_uri),
            _csv_safe(r.title),
            _csv_safe(r.year),
            _csv_safe(r.created_at.date().isoformat() if r.created_at else ""),
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="vectorbox-web-watches-{user_id}.csv"'
        },
    )


@router.post("/feed/reroll-cluster")
@limiter.limit("10/minute")
async def reroll_cluster(
    request: Request,
    current_user: TokenResponse = Depends(get_current_user),
):
    """Advance niche_theme_rotation and invalidate niche_picks cache."""
    user_id = current_user.user_id
    deleted = 0
    try:
        import redis.asyncio as aioredis
        import os
        from services.feed_service import FEED_CACHE_VERSION
        from services.recommendation_engine import GLOBAL_THEMES
        r = aioredis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379"),
            decode_responses=True,
        )
        try:
            from services.cache_service import scan_and_delete
            rotation_key = f"niche_theme_rotation:{FEED_CACHE_VERSION}:{user_id}"
            current = await r.get(rotation_key)
            n_themes = len(GLOBAL_THEMES)
            next_index = ((int(current) + 1) if current is not None else 1) % n_themes
            await r.setex(rotation_key, 60 * 60 * 24 * 7, str(next_index))

            deleted += await scan_and_delete(r, f"section:{FEED_CACHE_VERSION}:{user_id}:niche_picks:*")
            # Invalidate the full feed snapshot so the UI refetches sections
            await scan_and_delete(r, f"feed:{FEED_CACHE_VERSION}:{user_id}:*")
            logger.info(
                f"Niche theme reroll user {user_id}: theme → {next_index} "
                f"({GLOBAL_THEMES[next_index]['title']}), deleted {deleted} niche_picks keys"
            )
        finally:
            await r.close()
    except Exception as e:
        logger.warning(f"Niche theme reroll failed: {e}")

    return {"status": "ok", "message": "Niche theme will rotate on next feed load"}

