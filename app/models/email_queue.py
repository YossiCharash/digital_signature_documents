"""EmailQueue – a durable outbox for asynchronous email delivery.

Each row is a single email to send. The HTTP request only enqueues the row
(status ``queued``) and returns immediately; a background worker later claims
the row, sends it through the configured provider, and records the real
outcome. Because the whole message – including the signed document – is stored
in the row, delivery survives process restarts and does not depend on the
attachment still being available in S3.

Status lifecycle::

    queued  --worker claims-->  sending  --provider accepts-->  sent
                                    |
                                    +-- provider rejects, attempts left --> queued (retry after backoff)
                                    +-- provider rejects, no attempts left --> failed
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Status values (kept short so they fit the indexed column and read clearly
# in the delivery log).
STATUS_QUEUED = "queued"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"


class EmailQueue(Base):
    __tablename__ = "email_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- Recipient / message ---
    to_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    # "client" for the main recipient, "business" for the business copy.
    recipient_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(998), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reply_to: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # The signed document itself. Cleared (set to NULL) once the message is sent
    # so delivered rows do not keep the payload around.
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # URL the document can be fetched from (used by providers that attach by
    # URL rather than inline bytes, e.g. Pulseem's attchmentUrl).
    attachment_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Delivery state ---
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default=STATUS_QUEUED, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    # When the row becomes eligible for (re)sending. Used for backoff between
    # retries; the worker only claims rows whose time has come.
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def as_dict(self) -> dict:
        """Serialisable view – deliberately omits the binary ``content``."""
        return {
            "id": self.id,
            "to_email": self.to_email,
            "recipient_type": self.recipient_type,
            "filename": self.filename,
            "subject": self.subject,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "last_error": self.last_error,
            "provider_message_id": self.provider_message_id,
            "next_attempt_at": self.next_attempt_at.isoformat()
            if self.next_attempt_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }
