"""ShortLink – maps a 6-character slug to a long URL."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ShortLink(Base):
    __tablename__ = "short_links"

    slug: Mapped[str] = mapped_column(String(6), primary_key=True)
    long_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    tag: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
