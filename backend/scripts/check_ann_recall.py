"""HNSW health check: approximate vs exact KNN recall on the production index.

For each of the 7 curated anchors (same pool as TST-2), runs the same query
twice — once with the production HNSW params (`hnsw_ef=128`, `exact=False`)
and once with `exact=True` — then computes `recall@k` from the overlap of the
top-K result sets. Production target is >= 0.95.

Why this exists:
  - The qdrant-search-quality playbook recommends ANN-recall gating in CI.
  - If HNSW is silently dropping good hits, no other metric we track
    (hits@k, silhouette) will catch it.
  - Read-only — no DB writes, no Qdrant config changes.

Usage:
    docker compose exec backend python scripts/check_ann_recall.py [--k 10]
                                                                   [--hnsw-ef 128]
                                                                   [--limit 50]

Output: one line per anchor (recall@K + missing IDs), one summary line
with mean recall. Exit code 0 if mean >= 0.95, 1 otherwise — so this can
be wired into CI later without extra glue.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import or_, select
from qdrant_client.models import SearchParams

from config import AsyncSessionLocal
from models.database import Movie
from services.qdrant_service import QdrantService


ANCHORS = [
    "Howl's Moving Castle",
    "Deprisa, deprisa",
    "Pan's Labyrinth",
    "Inception",
    "The Godfather",
    "Goodfellas",
    "Spirited Away",
]


async def _resolve_anchor_tmdb_id(title: str) -> int | None:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(Movie.tmdb_id).where(
                or_(Movie.title.ilike(title), Movie.original_title.ilike(title))
            ).limit(1)
        )).first()
    return row[0] if row else None


async def _topk_ids(qd: QdrantService, vector: list[float], k: int, *, exact: bool, hnsw_ef: int) -> list[int]:
    """Return the top-(k+1) point IDs (rank 0 is the anchor itself; caller
    excludes it). We over-fetch by 1 so the comparison is apples-to-apples
    even when the anchor is in the catalog."""
    params = SearchParams(exact=True) if exact else SearchParams(hnsw_ef=hnsw_ef, exact=False)
    res = await qd.client.query_points(
        collection_name=qd.COLLECTION_NAME,
        query=vector,
        limit=k + 1,
        search_params=params,
    )
    return [int(p.id) for p in res.points]


async def main(k: int, hnsw_ef: int, limit_per_anchor: int) -> int:
    qd = QdrantService()
    overlaps: list[float] = []
    print(f"ANN-recall check: k={k}, hnsw_ef={hnsw_ef}, anchors={len(ANCHORS)}\n")

    for title in ANCHORS:
        tmdb_id = await _resolve_anchor_tmdb_id(title)
        if tmdb_id is None:
            print(f"  [skip] {title!r}: not in DB")
            continue
        vector = await qd.get_vector(tmdb_id)
        if vector is None:
            print(f"  [skip] {title!r}: no stored vector")
            continue

        approx = await _topk_ids(qd, vector, k, exact=False, hnsw_ef=hnsw_ef)
        exact = await _topk_ids(qd, vector, k, exact=True, hnsw_ef=hnsw_ef)

        # Drop the anchor itself (rank 0 in both) before computing recall.
        approx_set = set(approx[1:k + 1])
        exact_set = set(exact[1:k + 1])
        if not exact_set:
            print(f"  [skip] {title!r}: exact returned empty set")
            continue

        overlap = len(approx_set & exact_set) / len(exact_set)
        missing = exact_set - approx_set
        overlaps.append(overlap)
        marker = "OK " if overlap >= 0.95 else "LOW"
        missing_str = f"  missing_in_approx={sorted(missing)[:limit_per_anchor]}" if missing else ""
        print(f"  {marker}  {title!r:32s} recall@{k}={overlap:.3f}{missing_str}")

    if not overlaps:
        print("\nNo anchors evaluated. Catalog probably not seeded.")
        return 1

    mean = sum(overlaps) / len(overlaps)
    print(f"\nMean recall@{k} = {mean:.3f}  ({len(overlaps)}/{len(ANCHORS)} anchors evaluated)")
    target = 0.95
    if mean >= target:
        print(f"PASS (>= {target})")
        return 0
    print(f"FAIL (< {target}) — HNSW is dropping good hits; consider raising hnsw_ef or m")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=10, help="Top-K to compare (default: 10)")
    parser.add_argument("--hnsw-ef", type=int, default=128, help="Search-time ef (default: 128, prod baseline)")
    parser.add_argument("--limit", type=int, default=5, help="Max IDs to print per anchor for missing (default: 5)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.k, args.hnsw_ef, args.limit)))
