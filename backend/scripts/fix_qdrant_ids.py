"""
Fix Qdrant point IDs — migrate legacy points from internal Movie.id to Movie.tmdb_id.

Background
----------
VectorBox has two ingestion eras:
  - Legacy seed_db: point.id == Movie.id  (PostgreSQL internal PK)
  - Modern movie_factory: point.id == Movie.tmdb_id

AGENTS.md:245 mandates that Qdrant points are indexed by tmdb_id. The legacy
points cause silent misses in Signal A's centroid path because the recommender
queries `Movie.tmdb_id.in_(point_ids)` and legacy point.id values do not match
any tmdb_id.

Behavior
--------
For each Qdrant point we look up the DB:
  1. Already a tmdb_id (point.id == some Movie.tmdb_id) -> nothing to do.
  2. Internal id (point.id == some Movie.id) -> legacy. If a modern point with
     point.id == movie.tmdb_id already exists, just drop the legacy duplicate.
     Otherwise, re-upsert the vector under the correct tmdb_id, then drop the
     legacy point.
  3. Neither -> orphan. Logged. Pass --delete-orphans (with --execute) to wipe.

Usage
-----
    python scripts/fix_qdrant_ids.py                              # dry run (default)
    python scripts/fix_qdrant_ids.py --execute                    # apply migrations
    python scripts/fix_qdrant_ids.py --execute --delete-orphans   # also wipe orphans
    python scripts/fix_qdrant_ids.py --execute --limit 200
"""
import argparse
import asyncio
import logging
import os
import sys

# Make backend/ importable when run as `python scripts/fix_qdrant_ids.py`
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from qdrant_client.models import PointStruct, PointIdsList

from config import AsyncSessionLocal
from models.database import Movie
from services.qdrant_service import QdrantService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fix_qdrant_ids")


async def fix_qdrant_ids(
    dry_run: bool, limit: int | None, batch_size: int, delete_orphans: bool
) -> None:
    qdrant = QdrantService()
    collection = QdrantService.COLLECTION_NAME

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Movie.id, Movie.tmdb_id))
            rows = result.all()
            movies_by_tmdb = {row.tmdb_id: row.id for row in rows}
            movies_by_internal = {row.id: row.tmdb_id for row in rows}
            logger.info(f"Loaded {len(rows)} movies from DB")

        # First pass: enumerate every Qdrant point id so we know which tmdb_ids
        # already have a modern point. Without this we would re-upsert on top of
        # an existing modern point (idempotent, but wastes work) and worse,
        # could not safely tell legacy duplicates apart from cases that need
        # migration.
        existing_qdrant_ids: set[int] = set()
        offset = None
        while True:
            points, offset = await qdrant.client.scroll(
                collection_name=collection,
                limit=batch_size,
                offset=offset,
                with_vectors=False,
                with_payload=False,
            )
            if not points:
                break
            existing_qdrant_ids.update(p.id for p in points)
            if offset is None:
                break
        logger.info(f"Qdrant has {len(existing_qdrant_ids)} points total")

        # Second pass: classify, migrate, delete.
        offset = None
        total_checked = 0
        total_modern = 0
        total_legacy = 0
        total_migrated = 0
        total_deleted_dup = 0  # legacy duplicates dropped without re-upsert
        total_orphan = 0
        orphan_ids: list[int] = []

        while True:
            points, offset = await qdrant.client.scroll(
                collection_name=collection,
                limit=batch_size,
                offset=offset,
                with_vectors=True,
                with_payload=True,
            )
            if not points:
                break

            to_upsert: list[PointStruct] = []
            to_delete_legacy: list[int] = []

            for point in points:
                if limit is not None and total_checked >= limit:
                    break
                total_checked += 1
                pid = point.id

                if pid in movies_by_tmdb:
                    total_modern += 1
                    continue

                if pid in movies_by_internal:
                    total_legacy += 1
                    correct_tmdb = movies_by_internal[pid]
                    if correct_tmdb in existing_qdrant_ids:
                        total_deleted_dup += 1
                        to_delete_legacy.append(pid)
                        logger.debug(
                            f"Legacy duplicate: point.id={pid} -> tmdb_id={correct_tmdb} (already in Qdrant)"
                        )
                    else:
                        total_migrated += 1
                        to_upsert.append(
                            PointStruct(
                                id=correct_tmdb,
                                vector=point.vector,
                                payload=point.payload or {},
                            )
                        )
                        to_delete_legacy.append(pid)
                        existing_qdrant_ids.add(correct_tmdb)
                        logger.debug(
                            f"Legacy migrate: point.id={pid} -> tmdb_id={correct_tmdb}"
                        )
                else:
                    total_orphan += 1
                    orphan_ids.append(pid)
                    if total_orphan <= 10:
                        logger.warning(f"Orphan point: id={pid} (no DB record)")

            if not dry_run:
                if to_upsert:
                    await qdrant.client.upsert(
                        collection_name=collection,
                        points=to_upsert,
                    )
                if to_delete_legacy:
                    await qdrant.client.delete(
                        collection_name=collection,
                        points_selector=PointIdsList(points=to_delete_legacy),
                    )
                if to_upsert or to_delete_legacy:
                    logger.info(
                        f"Batch applied: upserted={len(to_upsert)} deleted={len(to_delete_legacy)}"
                    )

            logger.info(
                f"Progress checked={total_checked} modern={total_modern} "
                f"legacy={total_legacy} migrated={total_migrated} "
                f"dup_dropped={total_deleted_dup} orphans={total_orphan}"
            )

            if offset is None or (limit is not None and total_checked >= limit):
                break

        # Orphan cleanup runs after the scroll completes so we never delete a
        # point we have not finished classifying. Opt-in to keep accidental
        # invocations from wiping live data.
        orphans_deleted = 0
        if delete_orphans and orphan_ids and not dry_run:
            for i in range(0, len(orphan_ids), batch_size):
                chunk = orphan_ids[i : i + batch_size]
                await qdrant.client.delete(
                    collection_name=collection,
                    points_selector=PointIdsList(points=chunk),
                )
                orphans_deleted += len(chunk)
            logger.info(f"Deleted {orphans_deleted} orphan points")

        print("\n=== SUMMARY ===")
        print(f"Total points checked:        {total_checked}")
        print(f"Modern (tmdb_id, untouched): {total_modern}")
        print(f"Legacy (internal id):        {total_legacy}")
        print(f"  -> migrated to tmdb_id:    {total_migrated}")
        print(f"  -> dropped as duplicates:  {total_deleted_dup}")
        print(f"Orphans (no DB match):       {total_orphan}")
        if delete_orphans:
            if dry_run:
                print(f"  -> would delete:           {total_orphan} (dry run)")
            else:
                print(f"  -> deleted:                {orphans_deleted}")
        if dry_run:
            print("\nDRY RUN - no changes applied. Re-run with --execute to apply.")
        else:
            print("\nChanges applied.")
    finally:
        await qdrant.client.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Fix Qdrant point IDs (legacy internal -> tmdb_id).")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply changes. Without this flag the script runs in dry-run mode.",
    )
    parser.add_argument("--limit", type=int, help="Max points to process (default: all)")
    parser.add_argument("--batch-size", type=int, default=100, help="Scroll batch size")
    parser.add_argument(
        "--delete-orphans",
        action="store_true",
        help="Also delete points whose id matches no Movie record. Requires --execute.",
    )
    args = parser.parse_args()

    await fix_qdrant_ids(
        dry_run=not args.execute,
        limit=args.limit,
        batch_size=args.batch_size,
        delete_orphans=args.delete_orphans,
    )


if __name__ == "__main__":
    asyncio.run(main())
