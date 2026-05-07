"""
cleanup_anonymous_users.py — Nightly cron job to delete stale anonymous users.

Usage:
    python scripts/cleanup_anonymous_users.py [--dry-run] [--days N]

Default: deletes anonymous users inactive for 90+ days.
CASCADE on user_ratings FK handles associated data cleanup.
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta

from sqlalchemy import select, func, delete

# Ensure parent dir is on path for imports
sys.path.insert(0, "/app")

from config import AsyncSessionLocal
from models.database import User

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("cleanup_anonymous")

BATCH_SIZE = 100


async def cleanup(days: int, dry_run: bool):
    """Delete anonymous users whose last_active_at (or created_at) is older than `days` days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    logger.info(f"Cutoff: {cutoff.isoformat()} ({days} days ago)")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE DELETE'}")

    async with AsyncSessionLocal() as db:
        # Count candidates
        count_query = (
            select(func.count(User.id))
            .where(User.is_anonymous.is_(True))
            .where(
                # last_active_at < cutoff, or if NULL fall back to created_at
                func.coalesce(User.last_active_at, User.created_at) < cutoff
            )
        )
        total = await db.scalar(count_query)
        logger.info(f"Found {total} anonymous users inactive for {days}+ days")

        if total == 0 or dry_run:
            if dry_run and total > 0:
                # Show sample of users that would be deleted
                sample = await db.execute(
                    select(User.id, User.username, User.created_at, User.last_active_at)
                    .where(User.is_anonymous.is_(True))
                    .where(func.coalesce(User.last_active_at, User.created_at) < cutoff)
                    .limit(10)
                )
                for row in sample:
                    logger.info(
                        f"  [DRY RUN] Would delete: id={row.id} username={row.username} "
                        f"created={row.created_at} last_active={row.last_active_at}"
                    )
            return total

        # Batch delete
        deleted_total = 0
        while deleted_total < total:
            # Fetch batch of IDs
            batch_result = await db.execute(
                select(User.id)
                .where(User.is_anonymous.is_(True))
                .where(func.coalesce(User.last_active_at, User.created_at) < cutoff)
                .limit(BATCH_SIZE)
            )
            batch_ids = [row[0] for row in batch_result]

            if not batch_ids:
                break

            # CASCADE handles user_ratings, user_clusters, etc.
            await db.execute(
                delete(User).where(User.id.in_(batch_ids))
            )
            await db.commit()

            deleted_total += len(batch_ids)
            logger.info(f"  Deleted batch: {len(batch_ids)} users (total: {deleted_total}/{total})")

        logger.info(f"Cleanup complete. Deleted {deleted_total} anonymous users.")
        return deleted_total


def main():
    parser = argparse.ArgumentParser(description="Clean up stale anonymous users")
    parser.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    parser.add_argument("--days", type=int, default=90, help="Inactivity threshold in days (default: 90)")
    args = parser.parse_args()

    result = asyncio.run(cleanup(days=args.days, dry_run=args.dry_run))
    sys.exit(0 if result is not None else 1)


if __name__ == "__main__":
    main()
