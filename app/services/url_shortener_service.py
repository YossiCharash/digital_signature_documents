"""URL shortener service – creates and resolves short slugs backed by PostgreSQL."""

import secrets
import string
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.short_link import ShortLink
from app.utils.logger import logger

_ALPHABET = string.ascii_letters + string.digits  # a-z A-Z 0-9  (62 chars)

# Must match the width of ShortLink.slug. 12 characters over a 62-character
# alphabet: the slug is the only thing guarding a presigned URL to someone's
# signed document, so the space has to be too large to sweep. The original 6
# characters were enumerable.
SLUG_LENGTH = 12

_MAX_RETRIES = 10


def _generate_slug() -> str:
    """Return a cryptographically random alphanumeric slug."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(SLUG_LENGTH))


async def create_short_link(
    db: AsyncSession,
    long_url: str,
    tag: str,
    expires_in: int | None = None,
) -> ShortLink:
    """Persist a new ShortLink and return it.

    Retries up to _MAX_RETRIES times to handle the (extremely unlikely) slug collision.

    Args:
        db:         Active async SQLAlchemy session.
        long_url:   The full S3 presigned URL to shorten.
        tag:        Arbitrary label for tracking (e.g. business name or filename).
        expires_in: Lifetime in seconds. Should match the presigned URL's own
                    expiry so the slug cannot outlive the document it points at.

    Returns:
        The newly created ShortLink ORM object.

    Raises:
        RuntimeError: If a unique slug could not be generated after all retries.
    """
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None
    )

    for attempt in range(_MAX_RETRIES):
        slug = _generate_slug()
        existing = await db.get(ShortLink, slug)
        if existing is None:
            link = ShortLink(
                slug=slug, long_url=long_url, tag=tag, expires_at=expires_at
            )
            db.add(link)
            await db.commit()
            await db.refresh(link)
            logger.info("Created short link slug=%s tag=%s", slug, tag)
            return link
        logger.debug("Slug collision attempt %d: %s", attempt + 1, slug)

    raise RuntimeError(f"Could not generate a unique slug after {_MAX_RETRIES} attempts.")


async def get_short_link(db: AsyncSession, slug: str) -> ShortLink | None:
    """Look up a live ShortLink by its slug.

    Expired links are treated as missing, so callers cannot distinguish an
    expired slug from one that never existed.
    """
    link = await db.get(ShortLink, slug)
    if link is None:
        return None
    if link.is_expired():
        logger.info("Short link %s requested after expiry", slug)
        return None
    return link
