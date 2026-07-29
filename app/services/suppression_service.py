"""Suppression list service – the guard that keeps us off bad addresses.

Every send path calls :func:`is_suppressed` first; :func:`suppress` is called
by the SES feedback webhook (and the manual admin endpoint). When no database
is configured the list is simply empty and nothing is suppressed.
"""

from sqlalchemy import delete, func, select

from app.models.suppressed_email import REASON_MANUAL, SuppressedEmail
from app.utils.logger import logger


def _normalize(email: str) -> str:
    return (email or "").strip().lower()


async def is_suppressed(email: str) -> bool:
    """True if this address has bounced/complained and must not be emailed."""
    from app.db import async_session_factory

    if async_session_factory is None:
        return False
    normalized = _normalize(email)
    if not normalized:
        return False
    async with async_session_factory() as db:
        result = await db.execute(
            select(SuppressedEmail.id).where(SuppressedEmail.email == normalized)
        )
        return result.first() is not None


async def suppress(
    email: str,
    reason: str,
    detail: str | None = None,
    source: str | None = None,
) -> bool:
    """Add an address to the suppression list. Idempotent; never raises.

    Returns True if a new row was added, False if it was already present or the
    database is unavailable.
    """
    from app.db import async_session_factory

    if async_session_factory is None:
        return False
    normalized = _normalize(email)
    if not normalized:
        return False

    try:
        async with async_session_factory() as db:
            exists = await db.execute(
                select(SuppressedEmail.id).where(SuppressedEmail.email == normalized)
            )
            if exists.first() is not None:
                return False  # already suppressed – keep the original reason/detail
            db.add(
                SuppressedEmail(
                    email=normalized, reason=reason, detail=detail, source=source
                )
            )
            await db.commit()
            logger.info(
                "Suppressed %s (reason=%s, source=%s)", normalized, reason, source
            )
            return True
    except Exception as exc:
        # A unique-constraint race just means someone else suppressed it first.
        logger.warning("Failed to suppress %s: %s", normalized, exc)
        return False


async def unsuppress(email: str) -> bool:
    """Remove an address from the suppression list (false positive / recovery)."""
    from app.db import async_session_factory

    if async_session_factory is None:
        return False
    normalized = _normalize(email)
    async with async_session_factory() as db:
        result = await db.execute(
            delete(SuppressedEmail).where(SuppressedEmail.email == normalized)
        )
        await db.commit()
        removed = bool(result.rowcount)
        if removed:
            logger.info("Un-suppressed %s", normalized)
        return removed


async def add_manual(email: str, detail: str | None = None) -> bool:
    """Suppress an address by hand (e.g. a customer asked to stop)."""
    return await suppress(email, REASON_MANUAL, detail=detail, source="manual")


async def list_suppressed(
    reason: str | None = None, limit: int = 100
) -> list[dict]:
    """Return suppressed addresses, newest first."""
    from app.db import async_session_factory

    if async_session_factory is None:
        return []
    query = select(SuppressedEmail).order_by(SuppressedEmail.created_at.desc())
    if reason:
        query = query.where(SuppressedEmail.reason == reason)
    query = query.limit(limit)
    async with async_session_factory() as db:
        result = await db.execute(query)
        return [row.as_dict() for row in result.scalars().all()]


async def count_suppressed() -> int:
    """Total suppressed addresses (0 when DB is disabled)."""
    from app.db import async_session_factory

    if async_session_factory is None:
        return 0
    async with async_session_factory() as db:
        result = await db.execute(select(func.count(SuppressedEmail.id)))
        return int(result.scalar() or 0)
