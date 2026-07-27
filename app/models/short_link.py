"""ShortLink – maps a random slug to a long URL."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ShortLink(Base):
    __tablename__ = "short_links"

    slug: Mapped[str] = mapped_column(String(12), primary_key=True)
    long_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    tag: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # A short link wraps a presigned URL and must not outlive it: without this
    # the slug stays resolvable forever and keeps handing out a URL that only
    # stops working because S3 rejects it.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def is_expired(self, now: datetime | None = None) -> bool:
        """True when the link has passed its expiry. Links without one never expire."""
        if self.expires_at is None:
            return False
        reference = now or datetime.now(UTC)
        expires_at = self.expires_at
        # SQLite (used in tests) hands back naive datetimes even for
        # timezone-aware columns, so normalise before comparing.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= reference
