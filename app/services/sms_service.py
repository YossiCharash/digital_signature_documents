"""SMS delivery service – sends document download links via SMS."""

import httpx

from app.config import settings
from app.utils.errors import current_request_id
from app.utils.logger import logger

HTTP_TIMEOUT = 30.0

# Provider error bodies are echoed into the exception message, which reaches
# the application log. Cap them so a misbehaving provider cannot flood it.
MAX_ERROR_BODY = 500


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
        from_number: str | None = None,
    ):
        self.provider = provider or settings.sms_provider
        self.api_url = api_url or settings.sms_api_url
        self.api_key = api_key or settings.sms_api_key
        self.sender_name = sender_name or settings.sms_sender_name
        self.from_number = from_number or settings.sms_from_number

    async def send_document_link(
        self,
        to_phone: str,
        document_url: str,
        business_name: str | None = None,
        message: str | None = None,
    ) -> bool:
        """Send SMS with link to download document from S3."""
        try:
            logger.info("Sending document link via SMS to %s", to_phone)

            if self.provider == "api":
                return await self._send_via_api(
                    to_phone, document_url, business_name, message
                )
            raise SMSDeliveryError(f"Unknown SMS provider: {self.provider}")

        except SMSDeliveryError:
            raise
        except Exception as e:
            logger.error("SMS delivery failed: %s", e)
            raise SMSDeliveryError(f"SMS delivery failed: {e}") from e

    def _compose_message(
        self, document_url: str, business_name: str | None, message: str | None
    ) -> str:
        """Build the SMS text, preferring a caller-supplied message."""
        if message and message.strip():
            body = message.strip()
        elif business_name and business_name.strip():
            body = f"שלום, המסמך שלך מ-{business_name.strip()} מוכן להורדה."
        else:
            body = "שלום, המסמך שלך מוכן להורדה."

        if document_url:
            body += f"\nלהורדה: {document_url}"
        return body

    async def _send_via_api(
        self,
        to_phone: str,
        document_url: str,
        business_name: str | None,
        message: str | None,
    ) -> bool:
        if not self.api_url:
            raise SMSDeliveryError("SMS API URL not configured")
        if not self.api_key:
            raise SMSDeliveryError("SMS API key not configured")
        if not self.from_number:
            raise SMSDeliveryError("SMS_FROM_NUMBER not configured")

        sms_message = self._compose_message(document_url, business_name, message)

        payload = {
            "sendId": self.api_key,
            "isAsync": "true",
            "smsSendData": {
                "fromNumber": self.from_number,
                "toNumberList": [to_phone],
                # Carries the request id so a delivery report from the provider
                # can be traced back to the request that produced it.
                "referenceList": [current_request_id() or "unknown"],
                "textList": [sms_message],
                "isAutomaticUnsubscribeLink": "false",
            },
        }

        headers = {
            "APIKey": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        logger.debug("Sending SMS request to %s", self.api_url)

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            try:
                response = await client.post(self.api_url, json=payload, headers=headers)
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                detail = self._error_detail(e.response)
                logger.error(
                    "SMS delivery failed: %s (url=%s). Check SMS_API_KEY and SMS_API_URL.",
                    detail,
                    self.api_url,
                )
                raise SMSDeliveryError(f"SMS delivery failed: {detail}") from e
            except httpx.RequestError as e:
                logger.error("SMS API request failed: %s", e)
                raise SMSDeliveryError(f"SMS API request failed: {e}") from e

        logger.info("SMS with document link sent successfully")
        return True

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Extract the provider's error message, falling back to the raw body."""
        detail = f"Status {response.status_code}"
        try:
            body = response.json()
            if isinstance(body, dict):
                message = body.get("message") or body.get("error") or str(body)
                return f"{detail}: {message}"
            return f"{detail}: {body}"
        except Exception:
            text = response.text[:MAX_ERROR_BODY]
            return f"{detail}: {text}" if text else detail
