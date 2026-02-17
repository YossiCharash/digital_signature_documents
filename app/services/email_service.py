"""Email delivery service – sends documents as attachments (SMTP or API)."""

import asyncio
import html
import mimetypes
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import policy

from app.config import settings
from app.utils.logger import logger


class EmailDeliveryError(Exception):
    """Raised when email delivery fails."""

    pass


class EmailService:
    """Service for sending emails with document attachments (single responsibility)."""

    def __init__(
        self,
        provider: str | None = None,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        smtp_use_tls: bool | None = None,
        smtp_from_email: str | None = None,
        smtp_from_name: str | None = None,
        api_url: str | None = None,
        api_key: str | None = None,
    ):
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

    async def send_document(
        self,
        to_email: str,
        document: bytes,
        filename: str,
        subject: str | None = None,
        body: str | None = None,
        from_name: str | None = None,
        reply_to: str | None = None,
    ) -> bool:
        """Send document as email attachment."""
        try:
            logger.info(f"Sending document {filename} to {to_email}")

            return await self._send_document_via_smtp(
                to_email, document, filename, subject, body, from_name, reply_to
            )
        except EmailDeliveryError:
            raise EmailDeliveryError(f"Unknown email provider: {self.provider}")
        except Exception as e:
            logger.error(f"Email delivery failed: {e}")
            raise EmailDeliveryError(f"Email delivery failed: {e}") from e

    def _content_type_for(self, filename: str) -> str:
        ct, _ = mimetypes.guess_type(filename)
        return ct or "application/octet-stream"

    @staticmethod
    def _body_as_rtl_html(body: str) -> str:
        """Wrap plain body in HTML with dir=rtl and lang=he for RTL display in email clients."""
        escaped = html.escape(body)
        # Preserve line breaks for display
        with_br = escaped.replace("\n", "<br>\n")
        return (
            '<!DOCTYPE html>\n<html dir="rtl" lang="he">\n<head>\n'
            '<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width">\n'
            '</head>\n<body style="font-family: Arial, sans-serif;">\n'
            f'<div dir="rtl">{with_br}</div>\n</body>\n</html>'
        )

    def _ascii_fallback_filename(self, filename: str) -> str:
        """Return ASCII-only fallback filename for email clients."""
        # Replace non-ASCII characters with underscore
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename).strip()

        # If the result is mostly underscores (meaning original was mostly non-ASCII),
        # or empty, fall back to a generic name but keep extension if possible.
        base_match = re.match(r"^(.*)\.([a-zA-Z0-9]+)$", safe)
        if base_match:
            base, ext = base_match.groups()
        else:
            base, ext = safe, ""

        # Check if base is just underscores/dots or empty
        if not base or all(c in "_." for c in base):
            return f"document.{ext}" if ext else "document.pdf"

        return safe if ext else f"{safe}.pdf"

    async def _send_document_via_smtp(
        self,
        to_email: str,
        document: bytes,
        filename: str,
        subject: str | None,
        body: str | None,
        from_name: str | None,
        reply_to: str | None,
    ) -> bool:
        # Validate SMTP host configuration
        if not self.smtp_host or not self.smtp_host.strip():
            raise EmailDeliveryError(
                "SMTP host not configured. Please set SMTP_HOST in your .env file (e.g., SMTP_HOST=smtp.gmail.com)"
            )

        if not self.smtp_port:
            raise EmailDeliveryError(
                "SMTP port not configured. Please set SMTP_PORT in your .env file (e.g., SMTP_PORT=587)"
            )

        msg = MIMEMultipart(policy=policy.SMTP)
        if from_name is not None:
            effective_from_name = from_name.strip() if from_name else ""
        else:
            effective_from_name = (self.smtp_from_name or "").strip()

        if effective_from_name:
            from email.header import Header

            from_name_encoded = str(Header(effective_from_name, "utf-8"))
            msg["From"] = f"{from_name_encoded} {self.smtp_from_email}"
        else:
            msg["From"] = f"{self.smtp_from_email}"
        msg["To"] = to_email
        msg["Subject"] = subject or f"Document: {filename}"
        if reply_to and reply_to.strip():
            msg["Reply-To"] = reply_to.strip()

        email_body = body or f"Please find attached: {filename}."
        alt = MIMEMultipart("alternative", policy=policy.SMTP)
        alt.attach(MIMEText(email_body, "plain", "utf-8"))
        alt.attach(MIMEText(self._body_as_rtl_html(email_body), "html", "utf-8"))
        msg.attach(alt)

        if document:
            effective_filename = filename or "document.pdf"
            main_type, sub_type = self._content_type_for(effective_filename).split("/", 1)
            attachment = MIMEApplication(document, _subtype=sub_type)
            ascii_filename = self._ascii_fallback_filename(effective_filename)
            attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename=ascii_filename,
            )
            attachment.add_header(
                "Content-Type",
                f"{main_type}/{sub_type}",
                name=ascii_filename,
            )
            if effective_filename != ascii_filename:
                attachment.set_param(
                    "filename",
                    effective_filename,
                    header="Content-Disposition",
                    charset="utf-8",
                    language="",
                )
                attachment.set_param(
                    "name",
                    effective_filename,
                    header="Content-Type",
                    charset="utf-8",
                    language="",
                )
            msg.attach(attachment)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_smtp_sync, msg)
        logger.info(f"Document {filename} sent via SMTP to {to_email}")
        return True

    def _send_smtp_sync(self, msg: MIMEMultipart) -> None:
        if not self.smtp_host:
            raise EmailDeliveryError("SMTP host not configured")
        try:
            if self.smtp_use_tls:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port)

            if self.smtp_user and self.smtp_password:
                server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)
            server.quit()
        except OSError as e:
            error_code = getattr(e, "winerror", None) or getattr(e, "errno", None)
            if error_code == 11001 or (hasattr(e, "errno") and e.errno in (11001, -2, -3)):
                raise EmailDeliveryError(
                    f"Failed to resolve SMTP host '{self.smtp_host}'. "
                    f"Please check that SMTP_HOST is set correctly in your .env file. "
                    f"Common values: smtp.gmail.com, smtp.outlook.com, smtp.mail.yahoo.com"
                ) from e
            raise EmailDeliveryError(
                f"SMTP connection failed to {self.smtp_host}:{self.smtp_port}: {e}"
            ) from e
        except smtplib.SMTPAuthenticationError as e:
            raise EmailDeliveryError(
                "SMTP authentication failed. Please check SMTP_USER and SMTP_PASSWORD in your .env file"
            ) from e
        except smtplib.SMTPException as e:
            raise EmailDeliveryError(f"SMTP error: {e}") from e
