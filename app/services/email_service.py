"""Email delivery service – sends documents as attachments via Amazon SES."""

import asyncio
import html
import mimetypes
import re
import time
from email import policy
from email.message import EmailMessage

from app.config import settings
from app.utils.logger import logger

# Retry policy for transient SES failures (throttling, 5xx). Permanent failures
# (bad recipient, unverified sender) are not retried.
SES_MAX_ATTEMPTS = 3
SES_RETRY_BACKOFF = 2  # seconds, multiplied by attempt number

# Placeholder sender that ships as the default. Sending from it will be rejected
# by SES (unverified identity), so we warn when it is left unchanged.
PLACEHOLDER_FROM_EMAIL = "noreply@example.com"

# Palette for the HTML email template. Kept here (rather than in a stylesheet)
# because mail clients strip <style> blocks – every rule has to be inlined.
BRAND_COLOR = "#1f3864"  # deep navy: a business-correspondence header, flat
PAGE_BACKGROUND = "#f4f5f7"
CARD_BACKGROUND = "#ffffff"
CARD_BORDER = "#dfe3e8"
TEXT_COLOR = "#1f2430"
MUTED_TEXT_COLOR = "#6b7280"
ACCENT_BACKGROUND = "#f7f8fa"
ACCENT_BORDER = "#dfe3e8"
# The no-reply notice is set apart by weight and a border rather than by
# colour: an alarm-coloured panel is louder than a document notice warrants.
NOTICE_BACKGROUND = "#f7f8fa"
NOTICE_BORDER = "#c9ced6"
NOTICE_TEXT = "#33383f"

# Closing notice: these documents are sent from an unattended mailbox.
NO_REPLY_NOTICE = "הודעה זו נשלחה באופן אוטומטי – נא לא להשיב למייל זה."

# Header title used when the caller sends no business name.
DEFAULT_EMAIL_TITLE = "Nohalim"


class EmailDeliveryError(Exception):
    """Raised when email delivery fails."""

    pass


class EmailService:
    """Service for sending emails with document attachments via Amazon SES."""

    def __init__(
        self,
        provider: str | None = None,
        smtp_from_email: str | None = None,
        smtp_from_name: str | None = None,
        ses_region: str | None = None,
        ses_access_key: str | None = None,
        ses_secret_key: str | None = None,
        ses_configuration_set: str | None = None,
    ):
        self.provider = provider or settings.email_provider

        # Sender identity (the From address/name), shared with the signing
        # config. For SES this MUST be a verified identity.
        self.smtp_from_email = self._sanitize_address(
            smtp_from_email or settings.smtp_from_email
        )
        self.smtp_from_name = smtp_from_name or settings.smtp_from_name

        # Amazon SES – region falls back to the S3 region, then to boto3's
        # default resolution.
        self.ses_region = ses_region or settings.ses_region or settings.s3_region
        # Credentials: prefer dedicated SES keys, then reuse the S3 keys (same
        # AWS account, and they are already known to work), and finally fall
        # back to boto3's default chain / instance IAM role on AWS.
        self.ses_access_key = (
            ses_access_key or settings.ses_access_key or settings.s3_access_key
        )
        self.ses_secret_key = (
            ses_secret_key or settings.ses_secret_key or settings.s3_secret_key
        )
        self.ses_configuration_set = ses_configuration_set or settings.ses_configuration_set
        self._ses_client = None  # lazily created on first send

        # Fail-fast heads-up: the placeholder sender is not a verified SES
        # identity, so every send would be rejected. Warn loudly at startup
        # instead of only discovering it on the first failed request.
        if (
            not self.smtp_from_email
            or self.smtp_from_email.strip().lower() == PLACEHOLDER_FROM_EMAIL
        ):
            logger.warning(
                "SMTP_FROM_EMAIL is unset or still the placeholder "
                f"'{PLACEHOLDER_FROM_EMAIL}'. SES will reject every send until "
                "this is a verified sender identity."
            )

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
        """Send a document as an email attachment via Amazon SES."""
        try:
            logger.info(f"Sending document '{filename}' to {to_email} via SES")
            return await self._send_document_via_ses(
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
    def _body_as_plain_text(body: str) -> str:
        """Plain-text alternative, carrying the same no-reply notice as the HTML."""
        return f"{body.rstrip()}\n\n---\n{NO_REPLY_NOTICE}"

    @staticmethod
    def _body_as_rtl_html(
        body: str,
        sender_name: str | None = None,
        filename: str | None = None,
    ) -> str:
        """Render the body as a branded, RTL, mobile-friendly HTML email.

        Built from nested tables with fully inlined styles: mail clients (Outlook
        in particular) ignore <style> blocks, flexbox and modern CSS. The styling
        is deliberately plain – a flat navy header, hairline rules, no gradients
        or icons – so it reads as business correspondence rather than marketing.
        """
        paragraphs = [
            block.strip() for block in body.strip().split("\n\n") if block.strip()
        ]
        body_html = "\n".join(
            '<p style="margin:0 0 16px 0;font-size:16px;line-height:1.7;'
            f'color:{TEXT_COLOR};">'
            + html.escape(block).replace("\n", "<br>")
            + "</p>"
            for block in paragraphs
        )

        name = (sender_name or "").strip()
        title = html.escape(name) if name else DEFAULT_EMAIL_TITLE

        attachment_html = ""
        if filename:
            attachment_html = f"""
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="background-color:{ACCENT_BACKGROUND};border:1px solid {ACCENT_BORDER};
                            border-radius:4px;margin:8px 0 4px 0;">
                <tr>
                  <td style="padding:13px 16px;font-size:14px;line-height:1.6;
                             color:{TEXT_COLOR};" dir="rtl">
                    <span style="color:{MUTED_TEXT_COLOR};">מצורף למייל:</span>
                    <strong>{html.escape(filename)}</strong>
                  </td>
                </tr>
              </table>"""

        return f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background-color:{PAGE_BACKGROUND};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:{PAGE_BACKGROUND};padding:24px 12px;" dir="rtl">
  <tr>
    <td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             style="width:100%;max-width:600px;background-color:{CARD_BACKGROUND};
                    border:1px solid {CARD_BORDER};border-radius:6px;overflow:hidden;
                    font-family:'Segoe UI',Arial,Helvetica,sans-serif;">
        <tr>
          <td bgcolor="{BRAND_COLOR}" align="right"
              style="background-color:{BRAND_COLOR};padding:20px 28px;" dir="rtl">
            <div style="font-size:19px;font-weight:bold;color:#ffffff;">{title}</div>
          </td>
        </tr>
        <tr>
          <td style="padding:28px 28px 8px 28px;" dir="rtl" align="right">
            {body_html}
          </td>
        </tr>
        <tr>
          <td style="padding:0 28px;">{attachment_html}
          </td>
        </tr>
        <tr>
          <td style="padding:22px 28px 26px 28px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="background-color:{NOTICE_BACKGROUND};border:1px solid {NOTICE_BORDER};
                          border-radius:4px;">
              <tr>
                <td align="center" dir="rtl"
                    style="padding:14px 18px;font-size:14px;font-weight:bold;line-height:1.6;
                           color:{NOTICE_TEXT};letter-spacing:0.2px;">
                  {html.escape(NO_REPLY_NOTICE)}
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""

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

    @staticmethod
    def _sanitize_address(addr: str | None) -> str:
        """Strip surrounding whitespace and any control chars from an address.

        A stray newline/tab in an address makes SES reject the send with
        "Domain contains illegal character" and enables header injection, so we
        remove them before the address reaches SES (Source/Destinations) or the
        MIME headers.
        """
        return re.sub(r"[\r\n\t\x00-\x1f\x7f]", "", (addr or "").strip())

    def _effective_from_name(self, from_name: str | None) -> str:
        """Per-request sender name, falling back to the configured default.

        Control characters (CR/LF/etc.) are stripped: they would break header
        serialization and enable header injection.
        """
        name = (from_name or "").strip() or (self.smtp_from_name or "").strip()
        return re.sub(r"[\r\n\t\x00-\x1f\x7f]", " ", name).strip()

    def _build_message(
        self,
        to_email: str,
        document: bytes,
        filename: str,
        subject: str | None,
        body: str | None,
        from_name: str | None,
        reply_to: str | None,
    ) -> EmailMessage:
        """Build the MIME message sent through SES (send_raw_email)."""
        # בניית ההודעה באמצעות האובייקט המודרני
        msg = EmailMessage(policy=policy.SMTP)

        # --- Sender ---
        effective_from_name = self._effective_from_name(from_name)
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
        attached_name = (filename or "document.pdf") if document else None
        msg.set_content(self._body_as_plain_text(email_body))  # Plain text version
        msg.add_alternative(  # HTML RTL version
            self._body_as_rtl_html(email_body, effective_from_name, attached_name),
            subtype="html",
        )

        # --- Attachment ---
        if document:
            effective_filename = filename or "document.pdf"
            content_type = self._content_type_for(effective_filename)
            main_type, sub_type = content_type.split("/", 1)

            # הוספת הקובץ - פייתון תייצר את ה-Headers הנכונים לעברית באופן אוטומטי
            msg.add_attachment(
                document, maintype=main_type, subtype=sub_type, filename=effective_filename
            )

        return msg

    async def _send_document_via_ses(
        self,
        to_email: str,
        document: bytes,
        filename: str,
        subject: str | None,
        body: str | None,
        from_name: str | None,
        reply_to: str | None,
    ) -> bool:
        from_email = (self.smtp_from_email or "").strip()
        if not from_email or from_email.lower() == PLACEHOLDER_FROM_EMAIL:
            raise EmailDeliveryError(
                "SES requires a verified sender address; set SMTP_FROM_EMAIL to a "
                "verified SES identity."
            )

        # Clean the recipient too: a trailing newline in Destinations makes SES
        # reject the whole send with "Domain contains illegal character".
        to_email = self._sanitize_address(to_email)

        msg = self._build_message(
            to_email, document, filename, subject, body, from_name, reply_to
        )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_ses_sync, msg, to_email)
        logger.info(f"Document '{filename}' sent via SES to {to_email}")
        return True

    def _get_ses_client(self):  # type: ignore[no-untyped-def]
        """Lazily create (and cache) the boto3 SES client."""
        if self._ses_client is not None:
            return self._ses_client

        import boto3

        kwargs: dict = {}
        if self.ses_region:
            kwargs["region_name"] = self.ses_region
        # Only pass static credentials when explicitly configured; otherwise let
        # boto3 use the instance IAM role (App Runner / ECS / EC2).
        if self.ses_access_key and self.ses_secret_key:
            kwargs["aws_access_key_id"] = self.ses_access_key
            kwargs["aws_secret_access_key"] = self.ses_secret_key

        self._ses_client = boto3.client("ses", **kwargs)
        return self._ses_client

    def _send_ses_sync(self, msg: EmailMessage, to_email: str) -> None:
        from botocore.exceptions import (
            ClientError,
            ReadTimeoutError,
        )
        from botocore.exceptions import (
            ConnectionError as BotoConnectionError,
        )

        client = self._get_ses_client()

        last_error: Exception | None = None
        for attempt in range(1, SES_MAX_ATTEMPTS + 1):
            try:
                request = {
                    "Source": self.smtp_from_email,
                    "Destinations": [to_email],
                    "RawMessage": {"Data": msg.as_bytes()},
                }
                if self.ses_configuration_set:
                    request["ConfigurationSetName"] = self.ses_configuration_set
                client.send_raw_email(**request)
                if attempt > 1:
                    logger.info(f"SES delivery succeeded on attempt {attempt}")
                return
            except ClientError as e:
                if not self._is_transient_ses_error(e):
                    raise EmailDeliveryError(f"SES error: {e}") from e
                last_error = e
            except (BotoConnectionError, ReadTimeoutError) as e:
                # Only network/endpoint-level errors are transient. Permanent
                # config errors (NoCredentialsError, NoRegionError, ...) are
                # BotoCoreError but NOT connection errors, so they propagate
                # immediately instead of wasting the retry budget.
                last_error = e

            if attempt < SES_MAX_ATTEMPTS:
                delay = SES_RETRY_BACKOFF * attempt
                logger.warning(
                    f"SES delivery attempt {attempt} failed ({last_error}); "
                    f"retrying in {delay}s"
                )
                time.sleep(delay)

        raise EmailDeliveryError(
            f"SES error after {SES_MAX_ATTEMPTS} attempts: {last_error}"
        ) from last_error

    @staticmethod
    def _is_transient_ses_error(error: "Exception") -> bool:
        """Return True for temporary SES failures worth retrying."""
        code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
        # Throttling / rate limiting and internal 5xx errors are retryable;
        # MessageRejected, MailFromDomainNotVerified, etc. are permanent.
        return code in {
            "Throttling",
            "ThrottlingException",
            "TooManyRequestsException",
            "ServiceUnavailable",
            "InternalFailure",
        }
