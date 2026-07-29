"""Scheduler for periodic cleanup tasks."""

import asyncio

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.services.cleanup_service import CleanupService
from app.services.delivery_log_service import purge_old_successful_deliveries
from app.services.email_queue_service import (
    is_queue_enabled,
    process_pending_emails,
    purge_old_sent_emails,
)
from app.services.email_service import EmailService
from app.services.storage_service import StorageService
from app.utils.logger import logger


class SchedulerService:
    """Service for managing scheduled tasks."""

    def __init__(self, storage_service: StorageService):
        self.storage_service = storage_service
        self.cleanup_service = CleanupService(storage_service)
        self.scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Jerusalem"))
        # One shared EmailService for the worker so the SES client and settings
        # are reused across ticks rather than rebuilt every poll.
        self.email_service = EmailService()

    def start(self) -> None:
        """Start the scheduler and register the cleanup and email-queue jobs."""
        # Schedule cleanup job to run daily at midnight (00:00) Israel time
        self.scheduler.add_job(
            self._run_cleanup,
            trigger=CronTrigger(hour=0, minute=0, timezone="Asia/Jerusalem"),
            id="s3_cleanup",
            name="S3 Document Cleanup",
            replace_existing=True,
        )

        # Async email-delivery worker: drains the durable outbox. Only useful
        # when a database is configured (otherwise emails are sent inline).
        if is_queue_enabled():
            self.scheduler.add_job(
                self._run_email_queue,
                trigger=IntervalTrigger(seconds=settings.email_queue_poll_seconds),
                id="email_queue_worker",
                name="Async Email Queue Worker",
                replace_existing=True,
                # Never let two ticks overlap, and collapse any missed ticks
                # into one so a slow batch cannot pile up behind itself.
                max_instances=1,
                coalesce=True,
            )
            logger.info(
                "Email queue worker scheduled (every %ss)",
                settings.email_queue_poll_seconds,
            )

        self.scheduler.start()
        logger.info("Scheduler started - cleanup job scheduled for 00:00 Israel time daily")

    async def _run_email_queue(self) -> None:
        """Worker tick: send any due emails from the outbox."""
        try:
            await process_pending_emails(self.email_service)
        except Exception as e:
            logger.error(f"Error in email queue worker: {e}", exc_info=True)

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

        # Purge old successful delivery logs (failures are kept forever).
        try:
            deleted = await purge_old_successful_deliveries(
                settings.delivery_log_success_retention_days
            )
            logger.info(f"Delivery log purge completed: {deleted} successful entries removed")
        except Exception as e:
            logger.error(f"Error in delivery log purge job: {e}", exc_info=True)

        # Purge delivered email-queue rows (failed rows are kept for inspection).
        try:
            purged = await purge_old_sent_emails(settings.email_queue_sent_retention_days)
            logger.info(f"Email queue purge completed: {purged} sent rows removed")
        except Exception as e:
            logger.error(f"Error in email queue purge job: {e}", exc_info=True)
