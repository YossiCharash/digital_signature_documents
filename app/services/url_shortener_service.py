"""URL shortener service – creates and resolves short slugs backed by PostgreSQL."""

import secrets
import string

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.short_link import ShortLink
from app.utils.logger import logger

_ALPHABET = string.ascii_letters + string.digits  # a-z A-Z 0-9  (62 chars)
_SLUG_LENGTH = 6
_MAX_RETRIES = 10


def _generate_slug() -> str:
    """Return a cryptographically random alphanumeric string of length _SLUG_LENGTH."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(_SLUG_LENGTH))


async def create_short_link(db: AsyncSession, long_url: str, tag: str) -> ShortLink:
    """Persist a new ShortLink and return it.

    Retries up to _MAX_RETRIES times to handle the (extremely unlikely) slug collision.

    Args:
        db:       Active async SQLAlchemy session.
        long_url: The full S3 presigned URL to shorten.
        tag:      Arbitrary label for tracking (e.g. business name or filename).

    Returns:
        The newly created ShortLink ORM object.

    Raises:
        RuntimeError: If a unique slug could not be generated after all retries.
    """
    for attempt in range(_MAX_RETRIES):
        slug = _generate_slug()
        existing = await db.get(ShortLink, slug)
        if existing is None:
            link = ShortLink(slug=slug, long_url=long_url, tag=tag)
            db.add(link)
            await db.commit()
            await db.refresh(link)
            logger.info("Created short link slug=%s tag=%s", slug, tag)
            return link
        logger.debug("Slug collision attempt %d: %s", attempt + 1, slug)

    raise RuntimeError(f"Could not generate a unique slug after {_MAX_RETRIES} attempts.")


async def get_short_link(db: AsyncSession, slug: str) -> ShortLink | None:
    """Look up a ShortLink by its slug.  Returns None if not found."""
    return await db.get(ShortLink, slug)
