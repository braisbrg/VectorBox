"""Experiment: OLD vs NEW for 4 feed sections that today derive signal from clusters.

For each section, compares:
  - OLD: current code path (cluster.dominant_genres or medoid mean)
  - NEW: rating-aggregated derivation (no cluster indirection)

Sections covered:
  1. niche_picks       — fallback genres for cold-start
  2. hidden_gems       — score-to-hype filter scored by similarity to global center
  3. wildcard          — "Outside Your Comfort Zone" exclusion list
  4. upcoming          — filter upcoming films by user taste genres

Usage:
    docker compose exec -e PYTHONPATH=/app backend python scripts/experiment_feed_sections.py --user 212
"""
import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Set

import numpy as np
from sqlalchemy import select, or_, desc, func, cast
from sqlalchemy.dialects.postgresql import ARRAY

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
sys.path.append(backend_dir)

from config import AsyncSessionLocal
from models.database import UserRating, Movie, UserCluster
from services.qdrant_service import QdrantService


# ---------------------------------------------------------------------------
# Shared helpers — the candidates for the proposed fix.
# ---------------------------------------------------------------------------

def _recency_decay(ref, half_life_days: float = 730.0) -> float:
    if ref is None:
        return 0.5
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    days = max(0.0, (datetime.now(timezone.utc) - ref).total_seconds() / 86400.0)
    return 0.5 ** (days / half_life_days)


async def get_user_genre_preferences(
    user_id: int, db, *, min_rating: float = 3.5
) -> List[Tuple[str, float]]:
    """Returns [(genre, weight)] sorted desc. Weight is the sum of:
        (rating_part + liked_bonus + log1p(rewatch-1)*0.3) * recency_decay(730d)
    over every rated/liked film that has that genre."""
    result = await db.execute(
        select(UserRating, Movie)
        .join(Movie, UserRating.movie_id == Movie.id)
        .where(UserRating.user_id == user_id)
        .where(or_(UserRating.rating >= min_rating, UserRating.is_liked.is_(True)))
    )
    weights: Dict[str, float] = {}
    for ur, m in result.all():
        if not m.genres:
            continue
        base = max(0.0, ((ur.rating or 0) - 2.5) / 2.5) + (0.5 if ur.is_liked else 0.0)
        base += float(np.log1p(max(0, (ur.watch_count or 1) - 1))) * 0.3
        if base <= 0:
            continue
        w = base * _recency_decay(ur.created_at or ur.watched_date)
        for g in m.genres:
            weights[g] = weights.get(g, 0.0) + w
    return sorted(weights.items(), key=lambda x: -x[1])


async def quality_weighted_centroid(
    user_id: int, db, qdrant: QdrantService
) -> np.ndarray | None:
    """Returns a single centroid vector built from films ★≥4.0 OR is_liked,
    each weighted by (rating + liked) * recency_decay(540d).

    Used to replace `mean(medoid_vectors)` in hidden_gems where we need a
    SINGLE reference vector to score candidates by cosine similarity.
    """
    result = await db.execute(
        select(UserRating, Movie.tmdb_id)
        .join(Movie, UserRating.movie_id == Movie.id)
        .where(UserRating.user_id == user_id)
        .where(or_(UserRating.rating >= 4.0, UserRating.is_liked.is_(True)))
    )
    rows = result.all()
    if not rows:
        return None
    tmdb_ids = [tid for _, tid in rows]
    vecs_map = await qdrant.get_vectors_batch(tmdb_ids)

    weighted = []
    total_w = 0.0
    for ur, tid in rows:
        v = vecs_map.get(tid)
        if not v:
            continue
        base = max(0.0, ((ur.rating or 0) - 2.5) / 2.5) + (0.5 if ur.is_liked else 0.0)
        if base <= 0:
            continue
        w = base * _recency_decay(ur.created_at or ur.watched_date, half_life_days=540.0)
        weighted.append(np.array(v) * w)
        total_w += w
    if not weighted or total_w == 0:
        return None
    centroid = np.sum(np.stack(weighted), axis=0) / total_w
    norm = float(np.linalg.norm(centroid))
    return (centroid / norm) if norm > 0 else None


# ---------------------------------------------------------------------------
# Section comparisons
# ---------------------------------------------------------------------------

async def compare_niche_picks(user_id: int, db, qdrant: QdrantService):
    print("\n" + "=" * 78)
    print("  SECTION 1 — niche_picks (cold-start fallback genres)")
    print("=" * 78)

    # OLD: dominant_genres from BIGGEST cluster only
    clusters = (await db.execute(
        select(UserCluster).where(UserCluster.user_id == user_id)
        .order_by(desc(UserCluster.movie_count))
    )).scalars().all()
    old_genres: List[str] = []
    for c in clusters:
        if c.dominant_genres:
            old_genres = c.dominant_genres[:3]
            break
    if not old_genres:
        old_genres = ["Drama", "Thriller"]

    # NEW: top-3 genres by rating-weighted aggregation
    prefs = await get_user_genre_preferences(user_id, db)
    new_genres = [g for g, _ in prefs[:3]] or ["Drama", "Thriller"]

    print(f"  OLD genres (biggest cluster):  {old_genres}")
    print(f"  NEW genres (rating-weighted):  {new_genres}")
    print(f"  NEW weights: {[(g, round(w, 1)) for g, w in prefs[:6]]}")

    watched = set((await db.execute(
        select(UserRating.movie_id).where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
    )).scalars().all())

    async def _pick(genres):
        q = (
            select(Movie)
            .where(Movie.vectorbox_score > 70)
            .where(Movie.genres.overlap(genres))
            .where(Movie.id.notin_(watched) if watched else True)
            .order_by(desc(Movie.vectorbox_score))
            .limit(10)
        )
        return (await db.execute(q)).scalars().all()

    old_picks = await _pick(old_genres)
    new_picks = await _pick(new_genres)

    print(f"\n  OLD top-10 picks (avg VBS {np.mean([m.vectorbox_score for m in old_picks]):.1f}):")
    for m in old_picks:
        print(f"    {m.vectorbox_score:5.1f}  {m.title[:50]:<50}  {'/'.join(m.genres or [])[:35]}")
    print(f"\n  NEW top-10 picks (avg VBS {np.mean([m.vectorbox_score for m in new_picks]):.1f}):")
    for m in new_picks:
        print(f"    {m.vectorbox_score:5.1f}  {m.title[:50]:<50}  {'/'.join(m.genres or [])[:35]}")

    overlap = len({m.tmdb_id for m in old_picks} & {m.tmdb_id for m in new_picks})
    print(f"\n  Overlap OLD/NEW: {overlap}/10 films in common")


async def compare_hidden_gems(user_id: int, db, qdrant: QdrantService):
    print("\n" + "=" * 78)
    print("  SECTION 2 — hidden_gems (score-to-hype, scored by similarity-to-center)")
    print("=" * 78)

    # OLD: global_center = mean of cluster medoid vectors
    clusters = (await db.execute(
        select(UserCluster).where(UserCluster.user_id == user_id)
    )).scalars().all()
    old_center = None
    if clusters:
        medoid_ids = [c.medoid_movie_id for c in clusters if c.medoid_movie_id]
        if medoid_ids:
            rows = (await db.execute(
                select(Movie.tmdb_id).where(Movie.id.in_(medoid_ids))
            )).scalars().all()
            vmap = await qdrant.get_vectors_batch(rows)
            if vmap:
                old_center = np.mean(list(vmap.values()), axis=0)
                n = float(np.linalg.norm(old_center))
                if n > 0:
                    old_center = old_center / n

    # NEW: quality-weighted centroid
    new_center = await quality_weighted_centroid(user_id, db, qdrant)

    if old_center is None or new_center is None:
        print("  Cannot compute (missing vectors).")
        return

    cos_old_new = float(np.dot(old_center, new_center))
    print(f"  cosine(OLD center, NEW center) = {cos_old_new:.3f}")
    print(f"  (1.0 = identical, lower = different region of vector space)")

    # Candidate pool: same filters as production hidden_gems (popularity/quality)
    watched = set((await db.execute(
        select(UserRating.movie_id).where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
    )).scalars().all())
    candidates = (await db.execute(
        select(Movie)
        .where(Movie.vectorbox_score >= 65)
        .where(Movie.popularity <= 30)
        .where(Movie.vote_count >= 200)
        .where(Movie.id.notin_(watched) if watched else True)
        .order_by(desc(Movie.vectorbox_score))
        .limit(200)
    )).scalars().all()

    cand_tmdb_ids = [m.tmdb_id for m in candidates]
    cand_vecs = await qdrant.get_vectors_batch(cand_tmdb_ids)

    def _score(center, m):
        qual = (m.vectorbox_score or 0) / 100.0
        v = cand_vecs.get(m.tmdb_id)
        if v is None:
            sim = 0.5
        else:
            a = np.array(v)
            denom = float(np.linalg.norm(a))
            sim = float(np.dot(a, center) / denom) if denom > 0 else 0.5
        return qual * 0.7 + sim * 0.3

    old_ranked = sorted(candidates, key=lambda m: -_score(old_center, m))[:10]
    new_ranked = sorted(candidates, key=lambda m: -_score(new_center, m))[:10]

    print(f"\n  OLD top-10 (medoid-mean centroid, avg VBS {np.mean([m.vectorbox_score for m in old_ranked]):.1f}):")
    for m in old_ranked:
        print(f"    {m.vectorbox_score:5.1f}  {m.title[:50]:<50}  {'/'.join(m.genres or [])[:35]}")
    print(f"\n  NEW top-10 (quality-weighted centroid, avg VBS {np.mean([m.vectorbox_score for m in new_ranked]):.1f}):")
    for m in new_ranked:
        print(f"    {m.vectorbox_score:5.1f}  {m.title[:50]:<50}  {'/'.join(m.genres or [])[:35]}")

    overlap = len({m.tmdb_id for m in old_ranked} & {m.tmdb_id for m in new_ranked})
    print(f"\n  Overlap OLD/NEW: {overlap}/10 films in common")


async def compare_wildcard(user_id: int, db, qdrant: QdrantService):
    print("\n" + "=" * 78)
    print("  SECTION 3 — wildcard ('Outside Your Comfort Zone' exclusion list)")
    print("=" * 78)

    # OLD: union of dominant_genres from top-3 clusters
    clusters = (await db.execute(
        select(UserCluster).where(UserCluster.user_id == user_id)
        .order_by(desc(UserCluster.movie_count)).limit(3)
    )).scalars().all()
    old_excluded: Set[str] = set()
    for c in clusters:
        if c.dominant_genres:
            old_excluded.update(c.dominant_genres)

    # NEW: top-3 most-loved genres
    prefs = await get_user_genre_preferences(user_id, db)
    new_excluded = {g for g, _ in prefs[:3]}

    print(f"  OLD excluded ({len(old_excluded)} genres): {sorted(old_excluded)}")
    print(f"  NEW excluded ({len(new_excluded)} genres): {sorted(new_excluded)}")

    watched = set((await db.execute(
        select(UserRating.movie_id).where(UserRating.user_id == user_id, UserRating.is_watched.is_(True))
    )).scalars().all())

    async def _wildcard_sample(excluded):
        excluded_list = list(excluded)
        q = (
            select(Movie)
            .where(Movie.vectorbox_score >= 45)
            .where(Movie.vote_average > 7.0)
            .where(Movie.vote_count > 100)
            .where(~Movie.genres.overlap(excluded_list))
            .where(Movie.id.notin_(watched) if watched else True)
            .order_by(desc(Movie.vectorbox_score))
            .limit(10)
        )
        return (await db.execute(q)).scalars().all()

    old_pool_size = (await db.execute(
        select(func.count(Movie.id))
        .where(Movie.vectorbox_score >= 45, Movie.vote_average > 7.0, Movie.vote_count > 100)
        .where(~Movie.genres.overlap(list(old_excluded)))
    )).scalar()
    new_pool_size = (await db.execute(
        select(func.count(Movie.id))
        .where(Movie.vectorbox_score >= 45, Movie.vote_average > 7.0, Movie.vote_count > 100)
        .where(~Movie.genres.overlap(list(new_excluded)))
    )).scalar()
    print(f"  Candidate pool — OLD: {old_pool_size} films  |  NEW: {new_pool_size} films")

    old_picks = await _wildcard_sample(old_excluded)
    new_picks = await _wildcard_sample(new_excluded)

    print(f"\n  OLD top-10 wildcards (avg VBS {np.mean([m.vectorbox_score for m in old_picks]):.1f}):")
    for m in old_picks:
        print(f"    {m.vectorbox_score:5.1f}  {m.title[:50]:<50}  {'/'.join(m.genres or [])[:35]}")
    print(f"\n  NEW top-10 wildcards (avg VBS {np.mean([m.vectorbox_score for m in new_picks]):.1f}):")
    for m in new_picks:
        print(f"    {m.vectorbox_score:5.1f}  {m.title[:50]:<50}  {'/'.join(m.genres or [])[:35]}")


async def compare_upcoming(user_id: int, db, qdrant: QdrantService):
    print("\n" + "=" * 78)
    print("  SECTION 4 — upcoming (filter upcoming films by user taste)")
    print("=" * 78)

    clusters = (await db.execute(
        select(UserCluster).where(UserCluster.user_id == user_id)
    )).scalars().all()
    old_genres = list({g for c in clusters for g in (c.dominant_genres or [])})
    prefs = await get_user_genre_preferences(user_id, db)
    new_genres = [g for g, _ in prefs[:3]]

    print(f"  OLD genres (UNION of all clusters' dominant): {sorted(old_genres)}  ({len(old_genres)} genres)")
    print(f"  NEW genres (top-3 loved):                     {new_genres}")

    async def _upcoming(genres):
        q = (
            select(Movie)
            .where(Movie.is_upcoming.is_(True))
            .where(Movie.year.isnot(None))
            .where(Movie.popularity > 5.0)
        )
        if genres:
            q = q.where(Movie.genres.overlap(genres))
        q = q.order_by(desc(Movie.popularity)).limit(10)
        return (await db.execute(q)).scalars().all()

    old_pool = (await db.execute(
        select(func.count(Movie.id))
        .where(Movie.is_upcoming.is_(True), Movie.year.isnot(None), Movie.popularity > 5.0)
        .where(Movie.genres.overlap(old_genres) if old_genres else True)
    )).scalar()
    new_pool = (await db.execute(
        select(func.count(Movie.id))
        .where(Movie.is_upcoming.is_(True), Movie.year.isnot(None), Movie.popularity > 5.0)
        .where(Movie.genres.overlap(new_genres) if new_genres else True)
    )).scalar()
    print(f"  Upcoming pool — OLD filter: {old_pool}  |  NEW filter: {new_pool}")

    old_picks = await _upcoming(old_genres)
    new_picks = await _upcoming(new_genres)

    print(f"\n  OLD top-10 upcoming:")
    for m in old_picks:
        print(f"    pop={m.popularity:5.1f}  {m.title[:50]:<50}  {'/'.join(m.genres or [])[:35]}")
    print(f"\n  NEW top-10 upcoming:")
    for m in new_picks:
        print(f"    pop={m.popularity:5.1f}  {m.title[:50]:<50}  {'/'.join(m.genres or [])[:35]}")


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--user", type=int, required=True)
    args = p.parse_args()

    qdrant = QdrantService()
    try:
        async with AsyncSessionLocal() as db:
            await compare_niche_picks(args.user, db, qdrant)
            await compare_hidden_gems(args.user, db, qdrant)
            await compare_wildcard(args.user, db, qdrant)
            await compare_upcoming(args.user, db, qdrant)
    finally:
        await qdrant.client.close()


if __name__ == "__main__":
    asyncio.run(main())
