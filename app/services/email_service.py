"""Email delivery service with SMTP and API support."""

import asyncio
import json
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import httpx

from app.config import settings
from app.utils.logger import logger


class EmailDeliveryError(Exception):
    """Raised when email delivery fails."""

    pass


class EmailService:
    """Service for sending emails with invoice attachments."""

    def __init__(
        self,
        provider: Optional[str] = None,
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        smtp_use_tls: Optional[bool] = None,
        smtp_from_email: Optional[str] = None,
        smtp_from_name: Optional[str] = None,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """Initialize email service."""
        self.provider = provider or settings.email_provider
        self.smtp_host = smtp_host or settings.smtp_host
        self.smtp_port = smtp_port or settings.smtp_port
        self.smtp_user = smtp_user or settings.smtp_user
        self.smtp_password = smtp_password or settings.smtp_password
        self.smtp_use_tls = smtp_use_tls if smtp_use_tls is not None else settings.smtp_use_tls
        self.smtp_from_email = smtp_from_email or settings.smtp_from_email
        self.smtp_from_name = smtp_from_name or settings.smtp_from_name
        self.api_url = api_url or settings.email_api_url
        self.api_key = api_key or settings.email_api_key

    async def send_invoice(
        self,
        to_email: str,
        invoice_data: dict,
        invoice_number: str,
        subject: Optional[str] = None,
        body: Optional[str] = None,
    ) -> bool:
        """Send invoice via email."""
        try:
            logger.info(f"Sending invoice {invoice_number} to {to_email}")

            if self.provider == "smtp":
                return await self._send_via_smtp(to_email, invoice_data, invoice_number, subject, body)
            elif self.provider == "api":
                return await self._send_via_api(to_email, invoice_data, invoice_number, subject, body)
            else:
                raise EmailDeliveryError(f"Unknown email provider: {self.provider}")

        except Exception as e:
            logger.error(f"Email delivery failed: {e}")
            raise EmailDeliveryError(f"Email delivery failed: {e}") from e

    async def _send_via_smtp(
        self,
        to_email: str,
        invoice_data: dict,
        invoice_number: str,
        subject: Optional[str],
        body: Optional[str],
    ) -> bool:
        """Send email via SMTP."""
        if not self.smtp_host:
            raise EmailDeliveryError("SMTP host not configured")

        # Create message
        msg = MIMEMultipart()
        msg["From"] = f"{self.smtp_from_name} <{self.smtp_from_email}>"
        msg["To"] = to_email
        msg["Subject"] = subject or f"Digital Invoice {invoice_number}"

        # Add body
        email_body = body or f"Please find attached your digital invoice {invoice_number}."
        msg.attach(MIMEText(email_body, "plain"))

        # Add invoice as JSON attachment
        invoice_json = json.dumps(invoice_data, indent=2, ensure_ascii=False)
        attachment = MIMEApplication(invoice_json, _subtype="json")
        attachment.add_header(
            "Content-Disposition",
            f'attachment; filename="invoice_{invoice_number}.json"',
        )
        msg.attach(attachment)

        # Send via SMTP (run in executor since smtplib is blocking)
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, self._send_smtp_sync, msg, invoice_number
            )

            logger.info(f"Invoice {invoice_number} sent successfully via SMTP")
            return True

        except Exception as e:
            logger.error(f"SMTP send failed: {e}")
            raise EmailDeliveryError(f"SMTP send failed: {e}") from e

    def _send_smtp_sync(self, msg: MIMEMultipart, invoice_number: str) -> None:
        """Synchronous SMTP send (runs in executor)."""
        if self.smtp_use_tls:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port)

        if self.smtp_user and self.smtp_password:
            server.login(self.smtp_user, self.smtp_password)

        server.send_message(msg)
        server.quit()

    async def _send_via_api(
        self,
        to_email: str,
        invoice_data: dict,
        invoice_number: str,
        subject: Optional[str],
        body: Optional[str],
    ) -> bool:
        """Send email via API."""
        if not self.api_url:
            raise EmailDeliveryError("Email API URL not configured")
        if not self.api_key:
            raise EmailDeliveryError("Email API key not configured")

        # Prepare payload
        invoice_json = json.dumps(invoice_data, ensure_ascii=False)

        payload = {
            "to": to_email,
            "subject": subject or f"Digital Invoice {invoice_number}",
            "body": body or f"Please find attached your digital invoice {invoice_number}.",
            "attachments": [
                {
                    "filename": f"invoice_{invoice_number}.json",
                    "content": invoice_json,
                    "content_type": "application/json",
                }
            ],
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.api_url, json=payload, headers=headers, timeout=30.0)
                response.raise_for_status()

            logger.info(f"Invoice {invoice_number} sent successfully via API")
            return True

        except httpx.HTTPError as e:
            logger.error(f"Email API send failed: {e}")
            raise EmailDeliveryError(f"Email API send failed: {e}") from e
