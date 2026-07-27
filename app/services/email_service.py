"""Email delivery service – sends documents as attachments (SMTP or API)."""

import asyncio
import base64
import html
import mimetypes
import re
import smtplib
import time
from collections.abc import Callable
from email import policy
from email.message import EmailMessage
from typing import Any

from app.config import settings
from app.utils.logger import logger

# Network timeout (seconds) for the SMTP connection so a slow/unresponsive
# server can never hang the worker indefinitely.
SMTP_TIMEOUT = 30

# Retry policy for transient SMTP failures (greylisting, throttling,
# connection resets). Permanent failures (bad recipient, auth) are not retried.
SMTP_MAX_ATTEMPTS = 3
SMTP_RETRY_BACKOFF = 2  # seconds, multiplied by attempt number

# Placeholder sender that ships as the default. Sending from it will be rejected
# by SES/SendGrid (unverified identity), so we warn when it is left unchanged.
PLACEHOLDER_FROM_EMAIL = "noreply@example.com"

# Providers that require the From address to be a verified sender identity.
VERIFIED_SENDER_PROVIDERS = ("ses", "sendgrid", "mailjet")

# HTTP timeout (seconds) for the JSON-API providers (SendGrid, Mailjet).
HTTP_API_TIMEOUT = 30

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
        ses_region: str | None = None,
        ses_access_key: str | None = None,
        ses_secret_key: str | None = None,
        ses_configuration_set: str | None = None,
        sendgrid_api_key: str | None = None,
        sendgrid_api_url: str | None = None,
        sendgrid_sandbox_mode: bool | None = None,
        mailjet_api_key: str | None = None,
        mailjet_secret_key: str | None = None,
        mailjet_api_url: str | None = None,
        mailjet_sandbox_mode: bool | None = None,
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

        # SendGrid Web API v3.
        self.sendgrid_api_key = self._clean_secret(
            sendgrid_api_key or settings.sendgrid_api_key
        )
        self.sendgrid_api_url = (sendgrid_api_url or settings.sendgrid_api_url or "").strip()
        self.sendgrid_sandbox_mode = (
            sendgrid_sandbox_mode
            if sendgrid_sandbox_mode is not None
            else settings.sendgrid_sandbox_mode
        )

        # Mailjet Send API v3.1.
        self.mailjet_api_key = self._clean_secret(
            mailjet_api_key or settings.mailjet_api_key
        )
        self.mailjet_secret_key = self._clean_secret(
            mailjet_secret_key or settings.mailjet_secret_key
        )
        self.mailjet_api_url = (mailjet_api_url or settings.mailjet_api_url or "").strip()
        self.mailjet_sandbox_mode = (
            mailjet_sandbox_mode
            if mailjet_sandbox_mode is not None
            else settings.mailjet_sandbox_mode
        )

        # Fail-fast heads-up: the placeholder sender is not a verified sender
        # identity, so every send would be rejected. Warn loudly at startup
        # instead of only discovering it on the first failed request.
        if self.provider in VERIFIED_SENDER_PROVIDERS and (
            not self.smtp_from_email
            or self.smtp_from_email.strip().lower() == PLACEHOLDER_FROM_EMAIL
        ):
            logger.warning(
                f"EMAIL_PROVIDER={self.provider} but SMTP_FROM_EMAIL is unset or "
                f"still the placeholder '{PLACEHOLDER_FROM_EMAIL}'. "
                f"{self.provider} will reject every send until this is a "
                "verified sender identity."
            )

        if self.provider == "sendgrid":
            if not self.sendgrid_api_key:
                logger.warning(
                    "EMAIL_PROVIDER=sendgrid but SENDGRID_API_KEY is not set; "
                    "every send will fail."
                )
            elif not self.sendgrid_api_key.startswith("SG."):
                # Real keys are always "SG.<id>.<secret>". Anything else is
                # usually the API key *ID* from the dashboard list, or a
                # truncated paste – both fail at send time with a 401.
                logger.warning(
                    "SENDGRID_API_KEY does not start with 'SG.'; this looks like "
                    "the API key ID rather than the key itself. SendGrid will "
                    "reject it with 401."
                )

        if self.provider == "mailjet" and not (
            self.mailjet_api_key and self.mailjet_secret_key
        ):
            missing = [
                name
                for name, value in (
                    ("MAILJET_API_KEY", self.mailjet_api_key),
                    ("MAILJET_SECRET_KEY", self.mailjet_secret_key),
                )
                if not value
            ]
            logger.warning(
                f"EMAIL_PROVIDER=mailjet but {' and '.join(missing)} "
                "is not set; every send will fail."
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
        """Send document as email attachment."""
        try:
            logger.info(
                f"Sending document '{filename}' to {to_email} via {self.provider}"
            )
            if self.provider == "ses":
                return await self._send_document_via_ses(
                    to_email, document, filename, subject, body, from_name, reply_to
                )
            if self.provider == "sendgrid":
                return await self._send_document_via_sendgrid(
                    to_email, document, filename, subject, body, from_name, reply_to
                )
            if self.provider == "mailjet":
                return await self._send_document_via_mailjet(
                    to_email, document, filename, subject, body, from_name, reply_to
                )
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
    def _clean_secret(value: str | None) -> str | None:
        """Normalize a secret pasted into an env var / dashboard field.

        Strips surrounding whitespace (a trailing newline would make the
        Authorization header illegal) and matching quotes, which some hosting
        dashboards keep as part of the value.
        """
        if not value:
            return None
        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
            cleaned = cleaned[1:-1].strip()
        return cleaned or None

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
        """Build the MIME message shared by every delivery backend."""
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

            # add_attachment handles RFC 2231 encoding of the filename, so a
            # Hebrew attachment name needs no manual header construction.
            msg.add_attachment(
                document, maintype=main_type, subtype=sub_type, filename=effective_filename
            )

        return msg

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

        msg = self._build_message(
            to_email, document, filename, subject, body, from_name, reply_to
        )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_smtp_sync, msg)
        logger.info(f"Document '{filename}' sent via SMTP to {to_email}")
        return True

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

        msg = self._build_message(
            to_email, document, filename, subject, body, from_name, reply_to
        )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_ses_sync, msg, to_email)
        logger.info(f"Document '{filename}' sent via SES to {to_email}")
        return True

    async def _send_document_via_sendgrid(
        self,
        to_email: str,
        document: bytes,
        filename: str,
        subject: str | None,
        body: str | None,
        from_name: str | None,
        reply_to: str | None,
    ) -> bool:
        if not self.sendgrid_api_key:
            raise EmailDeliveryError("SENDGRID_API_KEY is not configured.")

        from_email = (self.smtp_from_email or "").strip()
        if not from_email or from_email.lower() == PLACEHOLDER_FROM_EMAIL:
            raise EmailDeliveryError(
                "SendGrid requires a verified sender address; set SMTP_FROM_EMAIL "
                "to a verified SendGrid sender identity."
            )

        payload = self._build_sendgrid_payload(
            to_email, document, filename, subject, body, from_name, reply_to
        )
        await self._send_sendgrid_request(payload)
        logger.info(f"Document '{filename}' sent via SendGrid to {to_email}")
        return True

    def _build_sendgrid_payload(
        self,
        to_email: str,
        document: bytes,
        filename: str,
        subject: str | None,
        body: str | None,
        from_name: str | None,
        reply_to: str | None,
    ) -> dict:
        """Build the JSON body for SendGrid's v3 mail/send endpoint."""
        email_body = body or f"Please find attached: {filename}."

        sender: dict = {"email": self.smtp_from_email}
        effective_from_name = self._effective_from_name(from_name)
        if effective_from_name:
            sender["name"] = effective_from_name

        attached_name = (filename or "document.pdf") if document else None
        payload: dict = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": sender,
            "subject": subject or f"Document: {filename}",
            # Order matters to SendGrid: plain text first, HTML last.
            "content": [
                {"type": "text/plain", "value": self._body_as_plain_text(email_body)},
                {
                    "type": "text/html",
                    "value": self._body_as_rtl_html(
                        email_body, effective_from_name, attached_name
                    ),
                },
            ],
        }

        if reply_to and reply_to.strip():
            payload["reply_to"] = {"email": reply_to.strip()}

        if document:
            effective_filename = filename or "document.pdf"
            payload["attachments"] = [
                {
                    "content": base64.b64encode(document).decode("ascii"),
                    "filename": effective_filename,
                    "type": self._content_type_for(effective_filename),
                    "disposition": "attachment",
                }
            ]

        if self.sendgrid_sandbox_mode:
            payload["mail_settings"] = {"sandbox_mode": {"enable": True}}

        return payload

    async def _send_sendgrid_request(self, payload: dict) -> None:
        headers = {
            "Authorization": f"Bearer {self.sendgrid_api_key}",
            "Content-Type": "application/json",
        }
        # 202 Accepted is SendGrid's success response, with an empty body.
        await self._post_json_with_retry(
            "SendGrid",
            self.sendgrid_api_url,
            payload,
            headers=headers,
            detail_fn=self._sendgrid_error_detail,
            hint_fn=self._sendgrid_hint,
        )

    async def _post_json_with_retry(
        self,
        provider: str,
        url: str,
        payload: dict,
        headers: dict | None = None,
        auth: tuple[str, str] | None = None,
        detail_fn: "Callable[[Any], str] | None" = None,
        hint_fn: "Callable[[int], str] | None" = None,
    ):  # type: ignore[no-untyped-def]
        """POST JSON to an email provider's API, retrying transient failures.

        Returns the successful response. Shared by every HTTP-API backend; only
        the error-extraction and hint callbacks differ between providers.
        """
        import httpx

        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=HTTP_API_TIMEOUT) as client:
            for attempt in range(1, SMTP_MAX_ATTEMPTS + 1):
                try:
                    response = await client.post(
                        url, json=payload, headers=headers, auth=auth
                    )
                    if response.is_success:
                        if attempt > 1:
                            logger.info(
                                f"{provider} delivery succeeded on attempt {attempt}"
                            )
                        return response

                    detail = (
                        detail_fn(response) if detail_fn else response.text[:500]
                    )
                    # 4xx other than 429 means a bad request/key/sender – retrying
                    # would never succeed.
                    if not self._is_transient_api_status(response.status_code):
                        hint = hint_fn(response.status_code) if hint_fn else ""
                        raise EmailDeliveryError(
                            f"{provider} error {response.status_code}: {detail}{hint}"
                        )
                    last_error = Exception(f"HTTP {response.status_code}: {detail}")
                except httpx.HTTPError as e:
                    # Network/timeout errors are transient.
                    last_error = e

                if attempt < SMTP_MAX_ATTEMPTS:
                    delay = SMTP_RETRY_BACKOFF * attempt
                    logger.warning(
                        f"{provider} delivery attempt {attempt} failed ({last_error}); "
                        f"retrying in {delay}s"
                    )
                    await asyncio.sleep(delay)

        raise EmailDeliveryError(
            f"{provider} error after {SMTP_MAX_ATTEMPTS} attempts: {last_error}"
        ) from last_error

    def _sendgrid_hint(self, status_code: int) -> str:
        """Actionable follow-up for the SendGrid failures that look alike."""
        if status_code == 401:
            return (
                " - SENDGRID_API_KEY is invalid, revoked, or from another account. "
                "Verify it with: curl -i https://api.sendgrid.com/v3/scopes "
                "-H 'Authorization: Bearer <key>'"
            )
        if status_code == 403:
            return (
                " - the API key is valid but lacks 'Mail Send' permission, or "
                f"'{self.smtp_from_email}' is not a verified SendGrid sender."
            )
        return ""

    @staticmethod
    def _is_transient_api_status(status_code: int) -> bool:
        """Return True for HTTP-API responses worth retrying."""
        return status_code == 429 or status_code >= 500

    @staticmethod
    def _sendgrid_error_detail(response) -> str:  # type: ignore[no-untyped-def]
        """Extract SendGrid's error messages, falling back to the raw body."""
        try:
            errors = response.json().get("errors", [])
            messages = [e.get("message", "") for e in errors if e.get("message")]
            if messages:
                return "; ".join(messages)
        except Exception:
            pass
        return response.text[:500]

    async def _send_document_via_mailjet(
        self,
        to_email: str,
        document: bytes,
        filename: str,
        subject: str | None,
        body: str | None,
        from_name: str | None,
        reply_to: str | None,
    ) -> bool:
        if not self.mailjet_api_key or not self.mailjet_secret_key:
            raise EmailDeliveryError(
                "Mailjet needs both MAILJET_API_KEY and MAILJET_SECRET_KEY."
            )

        from_email = (self.smtp_from_email or "").strip()
        if not from_email or from_email.lower() == PLACEHOLDER_FROM_EMAIL:
            raise EmailDeliveryError(
                "Mailjet requires a verified sender address; set SMTP_FROM_EMAIL "
                "to an address validated under Account > Sender domains."
            )

        payload = self._build_mailjet_payload(
            to_email, document, filename, subject, body, from_name, reply_to
        )
        response = await self._post_json_with_retry(
            "Mailjet",
            self.mailjet_api_url,
            payload,
            headers={"Content-Type": "application/json"},
            # Mailjet authenticates with HTTP Basic: API key as the user, secret
            # key as the password.
            auth=(self.mailjet_api_key, self.mailjet_secret_key),
            detail_fn=self._mailjet_error_detail,
            hint_fn=self._mailjet_hint,
        )
        # A 200 does not by itself mean the message was accepted: Send API v3.1
        # reports per-message failures inside the body.
        self._raise_on_mailjet_message_error(response)

        if self.mailjet_sandbox_mode:
            # Sandbox responses look exactly like successful ones, so say plainly
            # that nothing was delivered.
            logger.warning(
                f"MAILJET_SANDBOX_MODE is on: '{filename}' was validated for "
                f"{to_email} but NOT delivered. Set MAILJET_SANDBOX_MODE=false "
                "to send for real."
            )
            return True

        # The MessageID is the handle for tracing the message in Mailjet's
        # dashboard when the recipient reports it never arrived.
        message_id = self._mailjet_message_id(response)
        suffix = f" (Mailjet MessageID {message_id})" if message_id else ""
        logger.info(f"Document '{filename}' sent via Mailjet to {to_email}{suffix}")
        return True

    @staticmethod
    def _mailjet_message_id(response) -> str:  # type: ignore[no-untyped-def]
        """Pull the queued message's identifier out of a Mailjet 200 response."""
        try:
            messages = response.json().get("Messages", [])
            for message in messages:
                for target in message.get("To", []):
                    identifier = target.get("MessageID") or target.get("MessageUUID")
                    if identifier:
                        return str(identifier)
        except Exception:
            pass
        return ""

    def _build_mailjet_payload(
        self,
        to_email: str,
        document: bytes,
        filename: str,
        subject: str | None,
        body: str | None,
        from_name: str | None,
        reply_to: str | None,
    ) -> dict:
        """Build the JSON body for Mailjet's Send API v3.1."""
        email_body = body or f"Please find attached: {filename}."

        sender: dict = {"Email": self.smtp_from_email}
        effective_from_name = self._effective_from_name(from_name)
        if effective_from_name:
            sender["Name"] = effective_from_name

        attached_name = (filename or "document.pdf") if document else None
        message: dict = {
            "From": sender,
            "To": [{"Email": to_email}],
            "Subject": subject or f"Document: {filename}",
            "TextPart": self._body_as_plain_text(email_body),
            "HTMLPart": self._body_as_rtl_html(
                email_body, effective_from_name, attached_name
            ),
            # These are one-to-one transactional documents, not campaigns.
            # Open tracking injects a remote pixel and click tracking rewrites
            # links through Mailjet's domain – both are spam signals here, and
            # the stats are of no use for a single addressed document.
            "TrackOpens": "disabled",
            "TrackClicks": "disabled",
        }

        if reply_to and reply_to.strip():
            message["ReplyTo"] = {"Email": reply_to.strip()}

        if document:
            effective_filename = filename or "document.pdf"
            message["Attachments"] = [
                {
                    "ContentType": self._content_type_for(effective_filename),
                    "Filename": effective_filename,
                    "Base64Content": base64.b64encode(document).decode("ascii"),
                }
            ]

        payload: dict = {"Messages": [message]}
        if self.mailjet_sandbox_mode:
            payload["SandboxMode"] = True
        return payload

    @staticmethod
    def _raise_on_mailjet_message_error(response) -> None:  # type: ignore[no-untyped-def]
        """Surface per-message failures that Mailjet reports inside a 200."""
        try:
            messages = response.json().get("Messages", [])
        except Exception:
            return  # Unparseable body on a 2xx – treat as delivered.

        failures = [m for m in messages if m.get("Status") != "success"]
        if not failures:
            return

        details = []
        for failure in failures:
            for error in failure.get("Errors", []):
                text = error.get("ErrorMessage") or error.get("ErrorIdentifier", "")
                if text:
                    details.append(text)
        raise EmailDeliveryError(
            "Mailjet rejected the message: "
            + ("; ".join(details) or str(failures)[:500])
        )

    def _mailjet_hint(self, status_code: int) -> str:
        """Actionable follow-up for the Mailjet failures that look alike."""
        if status_code == 401:
            return (
                " - MAILJET_API_KEY / MAILJET_SECRET_KEY are wrong or belong to "
                "different keys. Both come from the same row in API Key "
                "Management; the secret is shown only when generated."
            )
        if status_code == 403:
            return (
                f" - the keys are valid but '{self.smtp_from_email}' is not an "
                "authorised sender under Account > Sender domains & addresses."
            )
        return ""

    @staticmethod
    def _mailjet_error_detail(response) -> str:  # type: ignore[no-untyped-def]
        """Extract Mailjet's error text, falling back to the raw body."""
        try:
            data = response.json()
        except Exception:
            return response.text[:500]

        # Top-level failures (auth, malformed request).
        for key in ("ErrorMessage", "ErrorInfo", "Message"):
            if data.get(key):
                return str(data[key])

        # Per-message failures.
        details = []
        for message in data.get("Messages", []):
            for error in message.get("Errors", []):
                text = error.get("ErrorMessage") or error.get("ErrorIdentifier", "")
                if text:
                    details.append(text)
        if details:
            return "; ".join(details)
        return response.text[:500]

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
        for attempt in range(1, SMTP_MAX_ATTEMPTS + 1):
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

            if attempt < SMTP_MAX_ATTEMPTS:
                delay = SMTP_RETRY_BACKOFF * attempt
                logger.warning(
                    f"SES delivery attempt {attempt} failed ({last_error}); "
                    f"retrying in {delay}s"
                )
                time.sleep(delay)

        raise EmailDeliveryError(
            f"SES error after {SMTP_MAX_ATTEMPTS} attempts: {last_error}"
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

    def _send_smtp_sync(self, msg: EmailMessage) -> None:
        if not self.smtp_host:
            raise EmailDeliveryError("SMTP host not configured")

        last_error: Exception | None = None
        for attempt in range(1, SMTP_MAX_ATTEMPTS + 1):
            try:
                self._deliver_once(msg)
                if attempt > 1:
                    logger.info(f"SMTP delivery succeeded on attempt {attempt}")
                return
            except smtplib.SMTPException as e:
                # Permanent failures should not be retried – retrying only
                # wastes time and can trip rate limits.
                if not self._is_transient_smtp_error(e):
                    raise EmailDeliveryError(f"SMTP error: {e}") from e
                last_error = e
            except OSError as e:
                # Socket/connection-level errors (timeout, reset) are transient.
                last_error = e

            if attempt < SMTP_MAX_ATTEMPTS:
                delay = SMTP_RETRY_BACKOFF * attempt
                logger.warning(
                    f"SMTP delivery attempt {attempt} failed ({last_error}); "
                    f"retrying in {delay}s"
                )
                time.sleep(delay)

        raise EmailDeliveryError(
            f"SMTP error after {SMTP_MAX_ATTEMPTS} attempts: {last_error}"
        ) from last_error

    def _deliver_once(self, msg: EmailMessage) -> None:
        """Open a fresh SMTP connection, send the message, and always close it."""
        if self.smtp_use_tls:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=SMTP_TIMEOUT)
        else:
            server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=SMTP_TIMEOUT)
        try:
            if self.smtp_use_tls:
                server.starttls()
            if self.smtp_user and self.smtp_password:
                server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                # quit() can raise if the connection is already broken; the
                # message was either delivered or will surface as a send error.
                try:
                    server.close()
                except Exception:
                    pass

    @staticmethod
    def _is_transient_smtp_error(error: smtplib.SMTPException) -> bool:
        """Return True for temporary SMTP failures that are worth retrying."""
        # Connection-level problems are always transient.
        if isinstance(
            error,
            (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, smtplib.SMTPHeloError),
        ):
            return True
        # 4xx response codes are "try again later" (e.g. greylisting, throttling).
        code = getattr(error, "smtp_code", None)
        if isinstance(code, int) and 400 <= code < 500:
            return True
        return False
