"""Email queue service – the durable-outbox worker behind async delivery.

The HTTP request enqueues a row and returns; this module owns everything that
happens afterwards: claiming rows safely (even with several workers running),
sending them through the configured :class:`EmailService`, retrying transient
failures with backoff, and writing the *real* delivery outcome to the delivery
log only once the provider has actually accepted the message.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.config import settings
from app.models.email_queue import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_SENDING,
    STATUS_SENT,
    EmailQueue,
)
from app.services.delivery_log_service import record_delivery
from app.services.email_service import EmailService
from app.utils.logger import logger

# A row stuck in "sending" for longer than this (worker crashed mid-send) is
# considered abandoned and returned to the queue so it is not lost.
STALE_SENDING_SECONDS = 600


def is_queue_enabled() -> bool:
    """True when messages should be queued rather than sent inline."""
    from app.db import async_session_factory

    return settings.email_queue_enabled and async_session_factory is not None


async def enqueue_email(
    to_email: str,
    document: bytes,
    filename: str,
    subject: str | None = None,
    body: str | None = None,
    from_name: str | None = None,
    reply_to: str | None = None,
    recipient_type: str | None = None,
) -> int | None:
    """Persist an email to send. Returns the queue row id, or None if disabled."""
    from app.db import async_session_factory

    if async_session_factory is None:
        return None

    row = EmailQueue(
        to_email=to_email,
        recipient_type=recipient_type,
        filename=filename,
        subject=subject,
        body=body,
        from_name=from_name,
        reply_to=reply_to,
        content=document,
        status=STATUS_QUEUED,
        max_attempts=settings.email_queue_max_attempts,
        next_attempt_at=datetime.now(UTC),
    )
    async with async_session_factory() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
        logger.info(
            "Email queued (id=%s) for %s [%s]", row.id, to_email, recipient_type or "-"
        )
        return row.id


def _backoff_seconds(attempts: int) -> int:
    """Exponential backoff (base * 2**(attempts-1)), capped at the configured max."""
    exp = settings.email_queue_retry_base_seconds * (2 ** max(0, attempts - 1))
    return min(exp, settings.email_queue_retry_max_seconds)


async def _reclaim_stale_sending() -> None:
    """Return rows stuck in 'sending' (crashed worker) back to the queue."""
    from app.db import async_session_factory

    if async_session_factory is None:
        return

    cutoff = datetime.now(UTC) - timedelta(seconds=STALE_SENDING_SECONDS)
    async with async_session_factory() as db:
        result = await db.execute(
            update(EmailQueue)
            .where(
                EmailQueue.status == STATUS_SENDING,
                EmailQueue.updated_at < cutoff,
            )
            .values(status=STATUS_QUEUED)
        )
        await db.commit()
        if result.rowcount:
            logger.warning(
                "Reclaimed %d stale 'sending' email(s) back to the queue",
                result.rowcount,
            )


async def _claim(db, row_id: int) -> bool:
    """Atomically move one queued row to 'sending'.

    The WHERE guard on the current status means only one worker can win the
    claim, so several workers (or overlapping ticks) never double-send a row.
    Returns True if *this* caller won the claim.
    """
    result = await db.execute(
        update(EmailQueue)
        .where(EmailQueue.id == row_id, EmailQueue.status == STATUS_QUEUED)
        .values(status=STATUS_SENDING, attempts=EmailQueue.attempts + 1)
    )
    await db.commit()
    return bool(result.rowcount)


async def process_pending_emails(email_service: EmailService | None = None) -> int:
    """Claim and send a batch of due emails. Returns how many were processed."""
    from app.db import async_session_factory

    if async_session_factory is None:
        return 0

    await _reclaim_stale_sending()

    service = email_service or EmailService()
    now = datetime.now(UTC)

    # Candidate rows: queued and due. We only read ids here; the actual claim is
    # a separate guarded UPDATE so the selection race is harmless.
    async with async_session_factory() as db:
        result = await db.execute(
            select(EmailQueue.id)
            .where(
                EmailQueue.status == STATUS_QUEUED,
                EmailQueue.next_attempt_at <= now,
            )
            .order_by(EmailQueue.next_attempt_at.asc(), EmailQueue.id.asc())
            .limit(settings.email_queue_batch_size)
        )
        candidate_ids = [r[0] for r in result.all()]

    processed = 0
    for row_id in candidate_ids:
        async with async_session_factory() as db:
            if not await _claim(db, row_id):
                continue  # someone else took it
            row = await db.get(EmailQueue, row_id)

        if row is None:
            continue
        await _send_claimed_row(service, row)
        processed += 1

    if processed:
        logger.info("Email queue worker processed %d message(s)", processed)
    return processed


async def _send_claimed_row(service: EmailService, row: EmailQueue) -> None:
    """Send one claimed row and persist the outcome (sent / retry / failed)."""
    from app.db import async_session_factory
    from app.services.suppression_service import is_suppressed

    assert async_session_factory is not None

    # Never send to an address that previously hard-bounced or complained: doing
    # so is what erodes sending reputation and gets accounts blocked.
    if await is_suppressed(row.to_email):
        reason = "recipient is on the suppression list (prior bounce/complaint)"
        async with async_session_factory() as db:
            await db.execute(
                update(EmailQueue)
                .where(EmailQueue.id == row.id)
                .values(status=STATUS_FAILED, last_error=reason, content=None)
            )
            await db.commit()
        await record_delivery(
            channel="email",
            recipient=row.to_email,
            recipient_type=row.recipient_type,
            filename=row.filename,
            subject=row.subject,
            status="failed",
            error=reason,
        )
        logger.warning("Skipped suppressed recipient %s (id=%s)", row.to_email, row.id)
        return

    try:
        await service.send_document(
            to_email=row.to_email,
            document=row.content or b"",
            filename=row.filename or "document.pdf",
            subject=row.subject,
            body=row.body,
            from_name=row.from_name,
            reply_to=row.reply_to,
        )
    except Exception as exc:  # EmailDeliveryError and anything unexpected
        await _handle_send_failure(row, exc)
        return

    # Success: record the *real* delivery only now, and drop the payload.
    async with async_session_factory() as db:
        await db.execute(
            update(EmailQueue)
            .where(EmailQueue.id == row.id)
            .values(
                status=STATUS_SENT,
                sent_at=datetime.now(UTC),
                content=None,
                last_error=None,
            )
        )
        await db.commit()
    await record_delivery(
        channel="email",
        recipient=row.to_email,
        recipient_type=row.recipient_type,
        filename=row.filename,
        subject=row.subject,
        status="sent",
    )
    logger.info("Email id=%s delivered to %s", row.id, row.to_email)


async def _handle_send_failure(row: EmailQueue, exc: Exception) -> None:
    """Reschedule a retryable failure, or mark the row failed when exhausted."""
    from app.db import async_session_factory

    assert async_session_factory is not None

    error = str(exc)
    exhausted = row.attempts >= row.max_attempts
    if exhausted:
        async with async_session_factory() as db:
            await db.execute(
                update(EmailQueue)
                .where(EmailQueue.id == row.id)
                .values(status=STATUS_FAILED, last_error=error)
            )
            await db.commit()
        await record_delivery(
            channel="email",
            recipient=row.to_email,
            recipient_type=row.recipient_type,
            filename=row.filename,
            subject=row.subject,
            status="failed",
            error=error,
        )
        logger.error(
            "Email id=%s to %s failed permanently after %d attempts: %s",
            row.id,
            row.to_email,
            row.attempts,
            error,
        )
        return

    delay = _backoff_seconds(row.attempts)
    retry_at = datetime.now(UTC) + timedelta(seconds=delay)
    async with async_session_factory() as db:
        await db.execute(
            update(EmailQueue)
            .where(EmailQueue.id == row.id)
            .values(status=STATUS_QUEUED, last_error=error, next_attempt_at=retry_at)
        )
        await db.commit()
    logger.warning(
        "Email id=%s to %s attempt %d/%d failed (%s); retrying in %ds",
        row.id,
        row.to_email,
        row.attempts,
        row.max_attempts,
        error,
        delay,
    )


async def get_email_job(job_id: int) -> dict | None:
    """Return the current status of a queued email, or None if not found."""
    from app.db import async_session_factory

    if async_session_factory is None:
        return None
    async with async_session_factory() as db:
        row = await db.get(EmailQueue, job_id)
        return row.as_dict() if row else None


async def list_email_jobs(
    status: str | None = None, limit: int = 100
) -> list[dict]:
    """Return queued/sent/failed email jobs, newest first."""
    from app.db import async_session_factory

    if async_session_factory is None:
        return []
    query = select(EmailQueue).order_by(EmailQueue.id.desc())
    if status:
        query = query.where(EmailQueue.status == status)
    query = query.limit(limit)
    async with async_session_factory() as db:
        result = await db.execute(query)
        return [row.as_dict() for row in result.scalars().all()]


async def purge_old_sent_emails(retention_days: int) -> int:
    """Delete delivered rows older than retention_days. Failed rows are kept."""
    from sqlalchemy import delete

    from app.db import async_session_factory

    if async_session_factory is None:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                delete(EmailQueue).where(
                    EmailQueue.status == STATUS_SENT,
                    EmailQueue.sent_at < cutoff,
                )
            )
            await db.commit()
            return result.rowcount or 0
    except Exception as exc:
        logger.warning("Email queue purge failed: %s", exc)
        return 0
