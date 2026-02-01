"""SMS delivery service – sends document download links via SMS."""

import httpx

from app.config import settings
from app.utils.logger import logger


class SMSDeliveryError(Exception):
    """Raised when SMS delivery fails."""

    pass


class SMSService:
    """Service for sending SMS with document download links (single responsibility)."""

    def __init__(
        self,
        provider: str | None = None,
        api_url: str | None = None,
        api_key: str | None = None,
        sender_name: str | None = None,
    ):
        self.provider = provider or settings.sms_provider
        self.api_url = api_url or settings.sms_api_url
        self.api_key = api_key or settings.sms_api_key
        self.sender_name = sender_name or settings.sms_sender_name

    async def send_document_link(
        self,
        to_phone: str,
        document_url: str,
        message: str | None = None,
    ) -> bool:
        """Send SMS with link to download document from S3."""
        try:
            logger.info(f"Sending document link via SMS to {to_phone}")

            if self.provider == "api":
                return await self._send_via_api(to_phone, document_url, message)
            raise SMSDeliveryError(f"Unknown SMS provider: {self.provider}")

        except SMSDeliveryError:
            raise
        except Exception as e:
            logger.error(f"SMS delivery failed: {e}")
            raise SMSDeliveryError(f"SMS delivery failed: {e}") from e

    async def _send_via_api(
        self,
        to_phone: str,
        document_url: str,
        message: str | None,
    ) -> bool:
        if not self.api_url:
            raise SMSDeliveryError("SMS API URL not configured")
        if not self.api_key:
            raise SMSDeliveryError("SMS API key not configured")
        normalized = self._normalize_phone(to_phone)
        sms_message = message or "שלום, המסמך שלך מוכן להורדה."
        if document_url:
            sms_message += f"\nלהורדה: {document_url}"

        payload = {
            "to": normalized,
            "message": sms_message,
            "sender": self.sender_name,
        }

        # Try different authentication formats for PulseEM
        # PulseEM might use X-API-Key, Authorization (with/without Bearer), or other formats

        # Check if API key already has a prefix
        if self.api_key.startswith("Bearer ") or self.api_key.startswith("bearer "):
            # Already formatted, use as-is
            headers = {
                "Authorization": self.api_key,
                "Content-Type": "application/json",
            }
        else:
            # Try X-API-Key first (common for PulseEM and similar APIs)
            headers = {
                "X-API-Key": self.api_key,
                "Content-Type": "application/json",
            }

        logger.debug(f"Sending SMS request to {self.api_url}")
        logger.debug(f"Headers: {list(headers.keys())}")
        logger.debug(f"Payload: {payload}")

        async with httpx.AsyncClient() as client:
            last_error = None
            try:
                response = await client.post(
                    self.api_url, json=payload, headers=headers, timeout=30.0
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                last_error = e
                # If 403/401, try different authentication formats
                if e.response.status_code in (401, 403):
                    # Try 1: Authorization header without Bearer (if we used X-API-Key)
                    if "X-API-Key" in headers:
                        logger.debug("X-API-Key failed, trying Authorization header without Bearer")
                        headers_retry = {
                            "Authorization": self.api_key,  # Direct key, no Bearer prefix
                            "Content-Type": "application/json",
                        }
                        try:
                            response = await client.post(
                                self.api_url, json=payload, headers=headers_retry, timeout=30.0
                            )
                            response.raise_for_status()
                            # Success with retry, continue normally
                            last_error = None
                        except httpx.HTTPStatusError as retry_error:
                            last_error = retry_error
                            # Try 2: Authorization header with Bearer token
                            if last_error.response.status_code in (401, 403):
                                logger.debug(
                                    "Authorization without Bearer failed, trying with Bearer token"
                                )
                                headers_retry2 = {
                                    "Authorization": f"Bearer {self.api_key}",
                                    "Content-Type": "application/json",
                                }
                                try:
                                    response = await client.post(
                                        self.api_url,
                                        json=payload,
                                        headers=headers_retry2,
                                        timeout=30.0,
                                    )
                                    response.raise_for_status()
                                    # Success with retry, continue normally
                                    last_error = None
                                except httpx.HTTPStatusError as retry_error2:
                                    last_error = retry_error2
            except httpx.RequestError as e:
                logger.error(f"SMS API request failed: {e}")
                raise SMSDeliveryError(f"SMS API request failed: {e}") from e

            # If we still have an error, handle it
            if last_error:
                error_detail = f"Status {last_error.response.status_code}"
                try:
                    error_body = last_error.response.json()
                    if isinstance(error_body, dict):
                        error_msg = (
                            error_body.get("message") or error_body.get("error") or str(error_body)
                        )
                        error_detail = f"{error_detail}: {error_msg}"
                    else:
                        error_detail = f"{error_detail}: {error_body}"
                except Exception:
                    # If response is not JSON, try text
                    try:
                        error_text = last_error.response.text[:500]  # Limit to first 500 chars
                        if error_text:
                            error_detail = f"{error_detail}: {error_text}"
                    except Exception:
                        pass

                logger.error(
                    f"SMS delivery failed: {error_detail}. "
                    f"URL: {self.api_url}, "
                    f"Check your SMS_API_KEY and SMS_API_URL configuration."
                )
                raise SMSDeliveryError(
                    f"SMS delivery failed: {error_detail}. "
                    f"Please verify your SMS_API_KEY and SMS_API_URL are correct. "
                    f"For 403 Forbidden errors, check API key permissions and authentication format."
                ) from last_error

        logger.info("SMS with document link sent successfully")
        return True

    def _normalize_phone(self, phone: str) -> str:
        digits = "".join(filter(str.isdigit, phone))
        if digits.startswith("0") and len(digits) == 10:
            return "972" + digits[1:]
        if digits.startswith("972") and len(digits) == 12:
            return digits
        if len(digits) == 9:
            return "972" + digits
        return digits
