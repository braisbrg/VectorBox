import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

async def run_trending_update():
    logger.info("Running scheduled job: Update Letterboxd Popular")
    try:
        from scripts.popular_scraper import scrape_letterboxd_popular
        await scrape_letterboxd_popular()
    except Exception as e:
        logger.error(f"Trending update failed: {e}")

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
