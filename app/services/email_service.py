"""Email delivery service – sends documents as attachments (SMTP or API)."""

import asyncio
import base64
import html
import mimetypes
import re
import smtplib
import time
from email import policy
from email.message import EmailMessage

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
VERIFIED_SENDER_PROVIDERS = ("ses", "sendgrid")

# HTTP timeout (seconds) for the SendGrid Web API call.
SENDGRID_TIMEOUT = 30


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

        payload: dict = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": sender,
            "subject": subject or f"Document: {filename}",
            # Order matters to SendGrid: plain text first, HTML last.
            "content": [
                {"type": "text/plain", "value": email_body},
                {"type": "text/html", "value": self._body_as_rtl_html(email_body)},
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
        import httpx

        headers = {
            "Authorization": f"Bearer {self.sendgrid_api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=SENDGRID_TIMEOUT) as client:
            for attempt in range(1, SMTP_MAX_ATTEMPTS + 1):
                try:
                    response = await client.post(
                        self.sendgrid_api_url, json=payload, headers=headers
                    )
                    # 202 Accepted is the success response; anything else 2xx is
                    # still fine.
                    if response.is_success:
                        if attempt > 1:
                            logger.info(
                                f"SendGrid delivery succeeded on attempt {attempt}"
                            )
                        return

                    detail = self._sendgrid_error_detail(response)
                    # 4xx other than 429 means a bad request/key/sender – retrying
                    # would never succeed.
                    if not self._is_transient_sendgrid_status(response.status_code):
                        raise EmailDeliveryError(
                            f"SendGrid error {response.status_code}: {detail}"
                            f"{self._sendgrid_hint(response.status_code)}"
                        )
                    last_error = Exception(f"HTTP {response.status_code}: {detail}")
                except httpx.HTTPError as e:
                    # Network/timeout errors are transient.
                    last_error = e

                if attempt < SMTP_MAX_ATTEMPTS:
                    delay = SMTP_RETRY_BACKOFF * attempt
                    logger.warning(
                        f"SendGrid delivery attempt {attempt} failed ({last_error}); "
                        f"retrying in {delay}s"
                    )
                    await asyncio.sleep(delay)

        raise EmailDeliveryError(
            f"SendGrid error after {SMTP_MAX_ATTEMPTS} attempts: {last_error}"
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
    def _is_transient_sendgrid_status(status_code: int) -> bool:
        """Return True for SendGrid responses worth retrying."""
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
            # EmailMessage תואם ל-send_message
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
