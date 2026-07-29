"""Amazon SNS message handling for SES bounce/complaint feedback.

SES publishes bounce and complaint events to an SNS topic; SNS delivers them to
our webhook as signed HTTP POSTs. This module:

  * verifies the SNS signature (so nobody can forge events and poison the
    suppression list),
  * auto-confirms the topic subscription,
  * turns a hard bounce or a complaint into a suppression-list entry.

Both SES delivery formats are handled: identity "feedback notifications"
(``notificationType``) and Configuration-Set event destinations
(``eventType``).
"""

import base64
import json
from urllib.parse import urlparse

from app.config import settings
from app.models.suppressed_email import REASON_BOUNCE, REASON_COMPLAINT
from app.services.suppression_service import suppress
from app.utils.logger import logger

# Fields (in this exact order) that make up the string SNS signs, per message
# type. See "Verifying the signatures of Amazon SNS messages" in the AWS docs.
_SIGNING_FIELDS = {
    "Notification": ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"],
    "SubscriptionConfirmation": [
        "Message",
        "MessageId",
        "SubscribeURL",
        "Timestamp",
        "Token",
        "TopicArn",
        "Type",
    ],
    "UnsubscribeConfirmation": [
        "Message",
        "MessageId",
        "SubscribeURL",
        "Timestamp",
        "Token",
        "TopicArn",
        "Type",
    ],
}

# Cache of downloaded signing certificates, keyed by URL.
_cert_cache: dict[str, bytes] = {}


class SNSVerificationError(Exception):
    """Raised when an SNS message fails signature/format validation."""


def _is_aws_host(url: str) -> bool:
    """Only ever fetch certs / confirm subscriptions on real AWS hosts."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    return host.endswith(".amazonaws.com") and urlparse(url).scheme == "https"


def _canonical_string(message: dict) -> bytes:
    msg_type = message.get("Type", "")
    fields = _SIGNING_FIELDS.get(msg_type)
    if not fields:
        raise SNSVerificationError(f"Unknown SNS message type: {msg_type!r}")
    parts = []
    for field in fields:
        # Subject is optional; skip it entirely when absent.
        if field not in message:
            continue
        parts.append(field)
        parts.append(message[field])
    return ("\n".join(parts) + "\n").encode("utf-8")


def _fetch_cert(url: str) -> bytes:
    if url in _cert_cache:
        return _cert_cache[url]
    import httpx

    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    _cert_cache[url] = resp.content
    return resp.content


def verify_signature(message: dict) -> None:
    """Raise SNSVerificationError unless the message's signature is valid."""
    cert_url = message.get("SigningCertURL", "")
    if not _is_aws_host(cert_url):
        raise SNSVerificationError(f"SigningCertURL is not an AWS host: {cert_url!r}")

    signature = message.get("Signature", "")
    if not signature:
        raise SNSVerificationError("Message has no Signature")

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.x509 import load_pem_x509_certificate

    cert = load_pem_x509_certificate(_fetch_cert(cert_url))
    public_key = cert.public_key()

    # SignatureVersion 1 = SHA1withRSA (legacy), 2 = SHA256withRSA.
    algo = hashes.SHA256() if message.get("SignatureVersion") == "2" else hashes.SHA1()
    try:
        public_key.verify(
            base64.b64decode(signature),
            _canonical_string(message),
            padding.PKCS1v15(),
            algo,
        )
    except InvalidSignature as exc:
        raise SNSVerificationError("SNS signature verification failed") from exc


async def _confirm_subscription(message: dict) -> None:
    """Auto-confirm a topic subscription by visiting the SubscribeURL."""
    subscribe_url = message.get("SubscribeURL", "")
    if not _is_aws_host(subscribe_url):
        raise SNSVerificationError(
            f"SubscribeURL is not an AWS host: {subscribe_url!r}"
        )
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(subscribe_url)
        resp.raise_for_status()
    logger.info("Confirmed SNS subscription for topic %s", message.get("TopicArn"))


async def _handle_ses_event(payload: dict) -> None:
    """Suppress recipients from a bounce (hard) or complaint notification."""
    # Identity feedback uses notificationType; event publishing uses eventType.
    event = payload.get("notificationType") or payload.get("eventType") or ""

    if event == "Bounce":
        bounce = payload.get("bounce", {})
        # Only permanent (hard) bounces are suppressed; transient bounces may
        # succeed on retry and must not be blocked.
        if bounce.get("bounceType") != "Permanent":
            logger.info(
                "Ignoring %s bounce (not permanent)", bounce.get("bounceType")
            )
            return
        subtype = bounce.get("bounceSubType", "")
        for r in bounce.get("bouncedRecipients", []):
            addr = r.get("emailAddress")
            if addr:
                detail = f"hard bounce ({subtype}): {r.get('diagnosticCode', '')}".strip()
                await suppress(addr, REASON_BOUNCE, detail=detail, source="ses")

    elif event == "Complaint":
        complaint = payload.get("complaint", {})
        ctype = complaint.get("complaintFeedbackType", "")
        for r in complaint.get("complainedRecipients", []):
            addr = r.get("emailAddress")
            if addr:
                await suppress(
                    addr, REASON_COMPLAINT, detail=f"complaint: {ctype}", source="ses"
                )

    else:
        # Delivery, Send, Open, Click, etc. – nothing to suppress.
        logger.debug("Ignoring SES event type: %s", event)


async def handle_sns_message(raw_body: bytes) -> dict:
    """Process one raw SNS POST body. Returns a small status dict.

    Raises SNSVerificationError on anything that fails validation so the caller
    can answer 400 (SNS will retry, but a persistently bad message is dropped).
    """
    try:
        message = json.loads(raw_body)
    except (ValueError, TypeError) as exc:
        raise SNSVerificationError("Body is not valid JSON") from exc

    if not isinstance(message, dict):
        raise SNSVerificationError("SNS message must be a JSON object")

    if settings.ses_sns_verify_signatures:
        verify_signature(message)

    msg_type = message.get("Type")

    if msg_type == "SubscriptionConfirmation":
        await _confirm_subscription(message)
        return {"status": "subscription_confirmed"}

    if msg_type == "UnsubscribeConfirmation":
        logger.warning("Received SNS UnsubscribeConfirmation for %s", message.get("TopicArn"))
        return {"status": "unsubscribe_acknowledged"}

    if msg_type == "Notification":
        # The SES event itself is a JSON string inside "Message".
        try:
            payload = json.loads(message.get("Message", "{}"))
        except (ValueError, TypeError):
            logger.warning("SNS Notification 'Message' was not valid JSON")
            return {"status": "ignored_unparseable_message"}
        await _handle_ses_event(payload)
        return {"status": "processed"}

    raise SNSVerificationError(f"Unsupported SNS message type: {msg_type!r}")
