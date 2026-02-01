"""Email document deliverer – sends document as attachment inside email."""

import re
from typing import Any

from app.delivery.abstractions import IDocumentDeliverer
from app.services.email_service import EmailDeliveryError, EmailService
from app.utils.logger import logger


def _sanitize_filename(name: str) -> str:
    """Keep only safe characters for filename."""
    return re.sub(r"[^\w.\-]", "_", name) or "document"


class EmailDocumentDeliverer(IDocumentDeliverer):
    """Delivers document via email as attachment (open/closed – extend by new deliverers)."""

    def __init__(self, email_service: EmailService | None = None):
        self._email = email_service or EmailService()

    async def deliver(
        self,
        document: bytes,
        filename: str,
        recipient: str,
        **kwargs: Any,
    ) -> bool:
        subject: str | None = kwargs.get("subject")
        body: str | None = kwargs.get("body")
        safe_name = _sanitize_filename(filename) or "document"
        try:
            return await self._email.send_document(
                to_email=recipient,
                document=document,
                filename=safe_name,
                subject=subject,
                body=body,
            )
        except EmailDeliveryError as e:
            logger.error(f"Email deliverer failed: {e}")
            raise
