import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from config import AsyncSessionLocal
from services.trending_service import TrendingService

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

async def run_trending_update():
    logger.info("Running scheduled job: Update Letterboxd Popular")
    async with AsyncSessionLocal() as db:
        service = TrendingService(db)
        try:
            await service.update_letterboxd_popular()
            await db.commit()
        except Exception as e:
            logger.error(f"Trending update failed: {e}")
        finally:
            await service.close()

def start_scheduler():
    # Run every day at 00:00 UTC
    scheduler.add_job(
        run_trending_update,
        CronTrigger(hour=0, minute=0),
        id="update_popular",
        replace_existing=True
    )
    scheduler.start()
    logger.info("Scheduler started.")
