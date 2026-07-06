"""DeliveryLog – records every outbound delivery (email/SMS) and why it failed."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)  # "email" / "sms"
    recipient: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    # "client" for the main recipient, "business" for the business copy
    recipient_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(998), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # "sent" / "failed"
    # Full failure reason (e.g. "SMTP error after 3 attempts: ..."); NULL when sent.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "channel": self.channel,
            "recipient": self.recipient,
            "recipient_type": self.recipient_type,
            "filename": self.filename,
            "subject": self.subject,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
