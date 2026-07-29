"""SuppressedEmail – addresses we must never send to again.

Populated automatically from SES bounce/complaint notifications (a hard bounce
or a spam complaint suppresses the address) and, optionally, by hand. The
sending path consults this list before every send, so a bad address is dropped
across *every* provider – protecting the domain's sending reputation, which is
exactly what AWS SES (and every other provider) requires.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Why an address is suppressed.
REASON_BOUNCE = "bounce"
REASON_COMPLAINT = "complaint"
REASON_MANUAL = "manual"


class SuppressedEmail(Base):
    __tablename__ = "suppressed_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Stored lower-cased so lookups are case-insensitive.
    email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )
    reason: Mapped[str] = mapped_column(String(20), nullable=False)  # bounce/complaint/manual
    # Free-text context: bounce subtype + diagnostic code, or a manual note.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # e.g. "ses"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "reason": self.reason,
            "detail": self.detail,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
