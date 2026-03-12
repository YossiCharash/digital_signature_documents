"""Email delivery service – sends documents as attachments (SMTP or API)."""

import asyncio
import html
import mimetypes
import re
import smtplib
from email import policy
from email.message import EmailMessage

from app.config import settings
from app.utils.logger import logger


class EmailDeliveryError(Exception):
    """Raised when email delivery fails."""

    pass


class EmailService:
    """Service for sending emails with document attachments."""

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
            logger.info(f"Sending document '{filename}' to {to_email}")
            return await self._send_document_via_smtp(
                to_email, document, filename, subject, body, from_name, reply_to
            )
        except EmailDeliveryError:
            raise
        except Exception as e:
            logger.error(f"Email delivery failed: {e}")
            raise EmailDeliveryError(f"Email delivery failed: {e}") from e

    def _content_type_for(self, filename: str) -> str:
        ct, _ = mimetypes.guess_type(filename)
        return ct or "application/octet-stream"

    @staticmethod
    def _body_as_rtl_html(body: str) -> str:
        """Wrap plain body in HTML with dir=rtl and lang=he for RTL display."""
        escaped = html.escape(body)
        with_br = escaped.replace("\n", "<br>\n")
        return (
            '<!DOCTYPE html>\n<html dir="rtl" lang="he">\n<head>\n'
            '<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width">\n'
            '</head>\n<body style="font-family: Arial, sans-serif;">\n'
            f'<div dir="rtl">{with_br}</div>\n</body>\n</html>'
        )

    @staticmethod
    def _ascii_fallback_filename(filename: str) -> str:
        """Transliterates Hebrew to Latin for legacy clients."""
        transliteration = {
            "א": "a",
            "ב": "b",
            "ג": "g",
            "ד": "d",
            "ה": "h",
            "ו": "v",
            "ז": "z",
            "ח": "ch",
            "ט": "t",
            "י": "y",
            "כ": "k",
            "ך": "k",
            "ל": "l",
            "מ": "m",
            "ם": "m",
            "נ": "n",
            "ן": "n",
            "ס": "s",
            "ע": "",
            "פ": "p",
            "ף": "f",
            "צ": "tz",
            "ץ": "tz",
            "ק": "k",
            "ר": "r",
            "ש": "sh",
            "ת": "t",
        }
        result = []
        for char in filename:
            if char in transliteration:
                result.append(transliteration[char])
            elif char.isascii() and (char.isalnum() or char in "._- "):
                result.append(char)
            else:
                result.append("_")

        safe = "".join(result).strip()
        safe = re.sub(r"[_\s]+", "_", safe)

        if not safe or all(c in "_." for c in safe):
            return "document.pdf"
        return safe

    @staticmethod
    def _content_disposition(filename: str) -> str:
        """
        Legacy helper. In the new version, EmailMessage handles this,
        but we keep the function to avoid breaking internal calls.
        """
        from email.header import Header

        encoded_filename = str(Header(filename, "utf-8"))
        return f'attachment; filename="{encoded_filename}"'

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
        if not self.smtp_host or not self.smtp_host.strip():
            raise EmailDeliveryError("SMTP host not configured.")

        # בניית ההודעה באמצעות האובייקט המודרני
        msg = EmailMessage(policy=policy.SMTP)

        # --- Sender ---
        effective_from_name = (from_name or "").strip() or (self.smtp_from_name or "").strip()
        if effective_from_name:
            msg["From"] = f"{effective_from_name} <{self.smtp_from_email}>"
        else:
            msg["From"] = self.smtp_from_email

        msg["To"] = to_email
        msg["Subject"] = subject or f"Document: {filename}"
        if reply_to and reply_to.strip():
            msg["Reply-To"] = reply_to.strip()

        # --- Body ---
        email_body = body or f"Please find attached: {filename}."
        msg.set_content(email_body)  # Plain text version
        msg.add_alternative(self._body_as_rtl_html(email_body), subtype="html")  # HTML RTL version

        # --- Attachment ---
        if document:
            effective_filename = filename or "document.pdf"
            content_type = self._content_type_for(effective_filename)
            main_type, sub_type = content_type.split("/", 1)

            # הוספת הקובץ - פייתון תייצר את ה-Headers הנכונים לעברית באופן אוטומטי
            msg.add_attachment(
                document, maintype=main_type, subtype=sub_type, filename=effective_filename
            )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_smtp_sync, msg)
        logger.info(f"Document '{filename}' sent via SMTP to {to_email}")
        return True

    def _send_smtp_sync(self, msg: EmailMessage) -> None:
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

            # EmailMessage תואם ל-send_message
            server.send_message(msg)
            server.quit()
        except Exception as e:
            raise EmailDeliveryError(f"SMTP error: {e}") from e
