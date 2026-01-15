"""SMS delivery service."""

from typing import Optional

import httpx

from app.config import settings
from app.utils.logger import logger


class SMSDeliveryError(Exception):
    """Raised when SMS delivery fails."""

    pass


class SMSService:
    """Service for sending SMS notifications."""

    def __init__(
        self,
        provider: Optional[str] = None,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        sender_name: Optional[str] = None,
    ):
        """Initialize SMS service."""
        self.provider = provider or settings.sms_provider
        self.api_url = api_url or settings.sms_api_url
        self.api_key = api_key or settings.sms_api_key
        self.sender_name = sender_name or settings.sms_sender_name

    async def send_invoice_reference(
        self,
        to_phone: str,
        invoice_number: str,
        invoice_id: str,
        message: Optional[str] = None,
    ) -> bool:
        """Send SMS with invoice reference (no invoice content)."""
        try:
            logger.info(f"Sending SMS for invoice {invoice_number} to {to_phone}")

            if self.provider == "api":
                return await self._send_via_api(to_phone, invoice_number, invoice_id, message)
            else:
                raise SMSDeliveryError(f"Unknown SMS provider: {self.provider}")

        except Exception as e:
            logger.error(f"SMS delivery failed: {e}")
            raise SMSDeliveryError(f"SMS delivery failed: {e}") from e

    async def _send_via_api(
        self,
        to_phone: str,
        invoice_number: str,
        invoice_id: str,
        message: Optional[str],
    ) -> bool:
        """Send SMS via API."""
        if not self.api_url:
            raise SMSDeliveryError("SMS API URL not configured")
        if not self.api_key:
            raise SMSDeliveryError("SMS API key not configured")

        # Normalize phone number
        normalized_phone = self._normalize_phone(to_phone)

        # Build message
        sms_message = (
            message
            or f"Your digital invoice {invoice_number} is ready. Reference ID: {invoice_id[:8]}"
        )

        # Prepare payload (generic format, adapt to provider)
        payload = {
            "to": normalized_phone,
            "message": sms_message,
            "sender": self.sender_name,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.api_url, json=payload, headers=headers, timeout=30.0
                )
                response.raise_for_status()

            logger.info(f"SMS for invoice {invoice_number} sent successfully")
            return True

        except httpx.HTTPError as e:
            logger.error(f"SMS API send failed: {e}")
            raise SMSDeliveryError(f"SMS API send failed: {e}") from e

    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number to international format."""
        # Remove non-digit characters
        digits = "".join(filter(str.isdigit, phone))

        # Handle Israeli numbers
        if digits.startswith("0") and len(digits) == 10:
            # Convert 0XXXXXXXXX to 972XXXXXXXXX
            return "972" + digits[1:]
        elif digits.startswith("972") and len(digits) == 12:
            return digits
        elif len(digits) == 9:
            # Assume Israeli number without leading 0
            return "972" + digits
        else:
            # Return as-is if format is unclear
            return digits
