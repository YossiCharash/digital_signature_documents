"""Scheduler for periodic cleanup tasks."""

import asyncio

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.services.cleanup_service import CleanupService
from app.services.storage_service import StorageService
from app.utils.logger import logger


class SchedulerService:
    """Service for managing scheduled tasks."""

    def __init__(self, storage_service: StorageService):
        self.storage_service = storage_service
        self.cleanup_service = CleanupService(storage_service)
        self.scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Jerusalem"))

    def start(self) -> None:
        """Start the scheduler and register cleanup job."""
        # Schedule cleanup job to run daily at midnight (00:00) Israel time
        self.scheduler.add_job(
            self._run_cleanup,
            trigger=CronTrigger(hour=0, minute=0, timezone="Asia/Jerusalem"),
            id="s3_cleanup",
            name="S3 Document Cleanup",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info("Scheduler started - cleanup job scheduled for 00:00 Israel time daily")

    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Scheduler shut down")

    async def _run_cleanup(self) -> None:
        """Run the cleanup job (called by scheduler)."""
        logger.info("Starting scheduled S3 cleanup job")
        try:
            # Run synchronous cleanup in executor
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self.cleanup_service.cleanup_old_documents)
            logger.info(f"Cleanup job completed: {result}")
        except Exception as e:
            logger.error(f"Error in scheduled cleanup job: {e}", exc_info=True)
