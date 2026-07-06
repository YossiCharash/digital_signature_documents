"""Delivery log service – persists every send attempt (email/SMS) to the database.

Recording is best-effort: a failure to write the log must never break or fail
the actual delivery, so all exceptions are swallowed and logged as warnings.
When DATABASE_URL is not configured the record is only written to the app log.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.models.delivery_log import DeliveryLog
from app.utils.logger import logger


async def record_delivery(
    channel: str,
    recipient: str,
    status: str,
    error: str | None = None,
    recipient_type: str | None = None,
    filename: str | None = None,
    subject: str | None = None,
) -> None:
    """Persist one delivery attempt. Never raises."""
    from app.db import async_session_factory

    if async_session_factory is None:
        logger.info(
            "Delivery log (DB disabled): channel=%s recipient=%s status=%s error=%s",
            channel,
            recipient,
            status,
            error,
        )
        return

    try:
        async with async_session_factory() as db:
            db.add(
                DeliveryLog(
                    channel=channel,
                    recipient=recipient,
                    recipient_type=recipient_type,
                    filename=filename,
                    subject=subject,
                    status=status,
                    error=error,
                )
            )
            await db.commit()
    except Exception as exc:
        logger.warning("Failed to write delivery log for %s: %s", recipient, exc)


async def list_deliveries(
    status: str | None = None,
    channel: str | None = None,
    recipient: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return delivery log entries, newest first. Empty list when DB is disabled."""
    from app.db import async_session_factory

    if async_session_factory is None:
        return []

    query = select(DeliveryLog).order_by(DeliveryLog.created_at.desc(), DeliveryLog.id.desc())
    if status:
        query = query.where(DeliveryLog.status == status)
    if channel:
        query = query.where(DeliveryLog.channel == channel)
    if recipient:
        query = query.where(DeliveryLog.recipient == recipient)
    query = query.limit(limit)

    async with async_session_factory() as db:
        result = await db.execute(query)
        return [row.as_dict() for row in result.scalars().all()]


async def purge_old_successful_deliveries(retention_days: int) -> int:
    """Delete successful delivery logs older than retention_days.

    Failed deliveries (status != 'sent') are never deleted, so they remain
    available for investigation indefinitely. Returns the number of rows
    removed (0 when DB is disabled). Never raises.
    """
    from app.db import async_session_factory

    if async_session_factory is None:
        return 0

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                delete(DeliveryLog).where(
                    DeliveryLog.status == "sent",
                    DeliveryLog.created_at < cutoff,
                )
            )
            await db.commit()
            deleted = result.rowcount or 0
            logger.info(
                "Delivery log purge: removed %d successful entries older than %s",
                deleted,
                cutoff.isoformat(),
            )
            return deleted
    except Exception as exc:
        logger.warning("Delivery log purge failed: %s", exc)
        return 0
