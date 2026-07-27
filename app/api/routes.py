"""API routes: send document via email or SMS."""
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.api.dependencies import (
    get_email_service,
    get_signing_service,
    get_sms_service,
    get_storage_service,
    require_api_key,
    require_api_key_when_configured,
)
from app.config import settings
from app.services.delivery_log_service import list_deliveries, record_delivery
from app.services.email_service import EmailDeliveryError, EmailService
from app.services.signing_service import SigningError, SigningService
from app.services.sms_service import SMSDeliveryError, SMSService
from app.services.storage_service import StorageError, StorageService
from app.services.url_shortener_service import create_short_link
from app.utils.audit import log_operation
from app.utils.errors import internal_error
from app.utils.logger import logger
from app.utils.storage_keys import build_object_key
from app.utils.uploads import read_pdf_upload
from app.utils.validators import validate_email, validate_phone_number

router = APIRouter(tags=["documents"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pdf_display_filename(original_filename: str) -> str:
    """Normalize a filename to a .pdf extension, for display back to the caller.

    This is only a label: the S3 key comes from build_object_key, which is
    generated rather than derived from caller input.
    """
    base, _ = (
        original_filename.rsplit(".", 1) if "." in original_filename else (original_filename, "")
    )
    return f"{base}.pdf" if base else "document.pdf"


def _email_attachment_filename(business_name: str | None, original_filename: str) -> str:
    """Name shown on the email attachment (not the S3 key - see storage_keys)."""
    if business_name and business_name.strip():
        safe = re.sub(r'[\\/:*?"<>|]', "_", business_name.strip()).strip()
        base = safe.rsplit(".", 1)[0] if "." in safe else safe
        if base:
            return f"{base}.pdf"

    cleaned = (original_filename or "").strip()
    if not cleaned or cleaned.lower() in ("noname", "unnamed"):
        logger.debug("Attachment filename fallback: empty/noname filename and no business_name")
        return "document.pdf"

    base = cleaned.rsplit(".", 1)[0] if "." in cleaned else cleaned
    result = f"{base}.pdf" if base else "document.pdf"
    return "document.pdf" if result.lower() == "noname.pdf" else result


def _sanitize(value: str | None) -> str | None:
    """Return None for blank or literal-'None' strings, otherwise return stripped value."""
    if value is None:
        return None
    stripped = str(value).strip()
    return None if stripped == "" or stripped.lower() == "none" else stripped


def _build_email_body(business_name: str | None, body: str | None) -> str:
    """Compose the email body text.

    The sender is deliberately not named here: the email template already
    shows the business name as its header, and it is the From name too, so
    repeating it in the opening line read as duplication.
    """
    body_text = (body or "").strip()
    business = (business_name or "").strip()
    intro = 'שלום רב!\n\nהמסמך מצו"ב למייל'

    if body_text and body_text.lower() != "none":
        # A body that already names the business is treated as self-contained;
        # anything else gets the standard greeting in front of it.
        if business and business not in body_text:
            return f"{intro}\n\n{body_text}"
        return body_text

    return f"{intro}\n\nתודה"


def _signature_metadata(signature_data: dict, original_filename: str) -> dict[str, str]:
    """S3 object metadata recorded alongside every signed upload."""
    return {
        "document-hash": signature_data["hash"],
        "document-signature": signature_data["signature"],
        "signature-algorithm": signature_data["algorithm"],
        "original-filename": original_filename,
        "signed-at": datetime.now(UTC).isoformat(),
    }


@router.post("/documents/sign-and-email", status_code=status.HTTP_200_OK)
async def sign_and_email(
    file: UploadFile = File(..., description="PDF document to sign and send"),
    email: str = Form(..., description="Recipient email"),
    subject: str | None = Form(None, description="Email subject"),
    so: str | None = Form(None, description="(legacy) Email subject"),
    body: str | None = Form(None, description="Email body"),
    client_name: str | None = Form(None, description="Client name for email body"),
    business_name: str | None = Form(None, description="Business name to include in email"),
    business_email: str | None = Form(None, description="Business email to also send document to"),
    caller: str | None = Depends(require_api_key_when_configured),
    signing_svc: SigningService = Depends(get_signing_service),
    storage_svc: StorageService = Depends(get_storage_service),
    email_svc: EmailService = Depends(get_email_service),
) -> dict:
    """Sign PDF, upload to S3, and send email with the signed document attached."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File must have a filename")
    if not validate_email(email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid email address")

    # Validate the business email up-front, before any delivery, so an invalid
    # value never aborts the request after the client email was already sent.
    b_email = _sanitize(business_email)
    if b_email and not validate_email(b_email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid business email address")

    content = await read_pdf_upload(file, settings.max_upload_bytes)

    effective_subject = subject or so

    # The S3 key is generated (unique, traversal-safe); the attachment name is
    # derived from the business name and is only what the recipient sees.
    s3_key = build_object_key(file.filename)
    attachment_filename = _email_attachment_filename(business_name, file.filename)
    email_body = _build_email_body(business_name, body=body)

    logger.info(
        "sign-and-email: business_name=%r, business_email=%r, recipient=%r, so=%r",
        business_name,
        b_email,
        email,
        so,
    )
    logger.info(
        "sign-and-email: s3_key=%r, attachment_filename=%r", s3_key, attachment_filename
    )

    try:
        signed_content, signature_data = signing_svc.sign_pdf(content)

        storage_svc.upload_file(
            content=signed_content,
            filename=s3_key,
            content_type="application/pdf",
            metadata=_signature_metadata(signature_data, file.filename),
        )
        download_url = storage_svc.generate_presigned_url(s3_key)

        email_subject = effective_subject or f"מסמך חתום: {attachment_filename}"

        logger.info("Sending email to client: %s, from_name: %r", email, business_name)
        try:
            await email_svc.send_document(
                to_email=email,
                document=signed_content,
                filename=attachment_filename,
                subject=email_subject,
                body=email_body,
                from_name=business_name,
                reply_to=b_email,
            )
        except EmailDeliveryError as e:
            await record_delivery(
                channel="email",
                recipient=email,
                recipient_type="client",
                filename=attachment_filename,
                subject=email_subject,
                status="failed",
                error=str(e),
            )
            raise
        await record_delivery(
            channel="email",
            recipient=email,
            recipient_type="client",
            filename=attachment_filename,
            subject=email_subject,
            status="sent",
        )
        logger.info("Successfully sent email to client: %s", email)

        # The client email was already delivered above. A failure to send the
        # business copy must NOT fail the whole request (that would make callers
        # retry and double-send to the client). Report it as a partial status.
        business_email_status: str | None = None
        if b_email:
            logger.info("Sending document copy to business email: %s", b_email)
            try:
                await email_svc.send_document(
                    to_email=b_email,
                    document=signed_content,
                    filename=attachment_filename,
                    subject=email_subject,
                    body=email_body,
                    from_name=business_name,
                    reply_to=b_email,
                )
                business_email_status = "sent"
                logger.info("Successfully sent document copy to business email: %s", b_email)
                await record_delivery(
                    channel="email",
                    recipient=b_email,
                    recipient_type="business",
                    filename=attachment_filename,
                    subject=email_subject,
                    status="sent",
                )
            except EmailDeliveryError as e:
                business_email_status = "failed"
                logger.error("Failed to send document to business email %s: %s", b_email, e)
                await record_delivery(
                    channel="email",
                    recipient=b_email,
                    recipient_type="business",
                    filename=attachment_filename,
                    subject=email_subject,
                    status="failed",
                    error=str(e),
                )
        else:
            logger.warning(
                "business_email not provided or empty (after sanitize), skipping business email copy"
            )

        audit_metadata = {
            "s3_key": s3_key,
            "signature": signature_data["signature"],
            **({"business_email": b_email} if b_email else {}),
            **({"business_name": business_name} if business_name else {}),
        }
        log_operation(
            operation="sign-and-email",
            document_hash=signature_data["hash"],
            recipient=email,
            filename=attachment_filename,
            metadata=audit_metadata,
        )

        return {
            "status": "signed_and_sent",
            "delivery": "email",
            "recipient": email,
            "filename": attachment_filename,
            "s3_key": s3_key,
            "download_url": download_url,
            "signature": {
                "hash": signature_data["hash"],
                "algorithm": signature_data["algorithm"],
            },
            **({"business_recipient": b_email} if b_email else {}),
            **({"business_email_status": business_email_status} if business_email_status else {}),
        }

    except SigningError as e:
        raise internal_error("Signing failed", e) from e
    except StorageError as e:
        raise internal_error("Document storage failed", e) from e
    except EmailDeliveryError as e:
        raise internal_error("Email delivery failed", e) from e
    except Exception as e:
        raise internal_error("Unexpected error", e) from e


@router.post("/documents/sign-and-sms", status_code=status.HTTP_200_OK)
async def sign_and_sms(
    file: UploadFile = File(..., description="PDF document to sign and send"),
    phone: str = Form(..., description="Recipient phone number"),
    message: str | None = Form(None, description="Optional SMS message"),
    business_name: str | None = Form(None, description="Business name"),
    caller: str | None = Depends(require_api_key_when_configured),
    signing_svc: SigningService = Depends(get_signing_service),
    storage_svc: StorageService = Depends(get_storage_service),
    sms_svc: SMSService = Depends(get_sms_service),
) -> dict:
    """Sign PDF, upload to S3, and send SMS with download link."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File must have a filename")
    if not validate_phone_number(phone):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid phone number")

    content = await read_pdf_upload(file, settings.max_upload_bytes)

    # Label shown back to the caller; the S3 key is generated separately.
    pdf_filename = _pdf_display_filename(file.filename)

    try:
        signed_content, signature_data = signing_svc.sign_pdf(content)

        s3_key = build_object_key(file.filename)
        storage_svc.upload_file(
            content=signed_content,
            filename=s3_key,
            content_type="application/pdf",
            metadata=_signature_metadata(signature_data, file.filename),
        )
        download_url = storage_svc.generate_presigned_url(s3_key)

        short_url = await _shorten_download_url(download_url, business_name or pdf_filename)

        try:
            await sms_svc.send_document_link(
                to_phone=phone,
                document_url=short_url,
                message=message,
                business_name=business_name,
            )
        except SMSDeliveryError as e:
            await record_delivery(
                channel="sms",
                recipient=phone,
                recipient_type="client",
                filename=pdf_filename,
                status="failed",
                error=str(e),
            )
            raise
        await record_delivery(
            channel="sms",
            recipient=phone,
            recipient_type="client",
            filename=pdf_filename,
            status="sent",
        )

        log_operation(
            operation="sign-and-sms",
            document_hash=signature_data["hash"],
            recipient=phone,
            filename=pdf_filename,
            metadata={
                "s3_key": s3_key,
                "signature": signature_data["signature"],
                "short_url": short_url,
            },
        )

        return {
            "status": "signed_and_sent",
            "delivery": "sms",
            "recipient": phone,
            "filename": pdf_filename,
            "s3_key": s3_key,
            "download_url": download_url,
            "short_url": short_url,
            "signature": {
                "hash": signature_data["hash"],
                "algorithm": signature_data["algorithm"],
            },
        }

    except SigningError as e:
        raise internal_error("Signing failed", e) from e
    except StorageError as e:
        raise internal_error("Document storage failed", e) from e
    except SMSDeliveryError as e:
        raise internal_error("SMS delivery failed", e) from e
    except Exception as e:
        raise internal_error("Unexpected error", e) from e


async def _shorten_download_url(download_url: str, tag: str) -> str:
    """Wrap a presigned URL in an internal short link, when the DB is configured.

    Falls back to the original URL on any failure: a long link still works, and
    the SMS matters more than its tidiness. The short link is given the same
    lifetime as the presigned URL it wraps.
    """
    from app.db import async_session_factory

    if async_session_factory is None:
        return download_url

    try:
        async with async_session_factory() as db:
            link = await create_short_link(
                db,
                long_url=download_url,
                tag=tag,
                expires_in=settings.effective_short_link_expiration,
            )
        base = settings.api_url.strip().rstrip("/")
        short_url = f"{base}/r/{link.slug}"
        logger.info("Short URL created: %s (tag=%s)", short_url, tag)
        return short_url
    except Exception as exc:
        logger.warning("URL shortening failed, falling back to original URL: %s", exc)
        return download_url


@router.post("/documents/verify-signature", status_code=status.HTTP_200_OK)
async def verify_document_signature(
    file: UploadFile = File(..., description="PDF document to verify"),
    caller: str | None = Depends(require_api_key_when_configured),
    signing_svc: SigningService = Depends(get_signing_service),
) -> dict:
    """Verify digital signature of a PDF document."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File must have a filename")

    content = await read_pdf_upload(file, settings.max_upload_bytes)

    try:
        verification_result = signing_svc.verify_pdf_signature(content)
        return {"filename": file.filename, "verification": verification_result}
    except SigningError as e:
        raise internal_error("Verification failed", e) from e


@router.get("/deliveries", status_code=status.HTTP_200_OK)
async def get_deliveries(
    delivery_status: str | None = Query(
        None, alias="status", description="Filter by status: 'sent' or 'failed'"
    ),
    channel: str | None = Query(None, description="Filter by channel: 'email' or 'sms'"),
    recipient: str | None = Query(None, description="Filter by exact recipient (email/phone)"),
    limit: int = Query(100, ge=1, le=1000, description="Max rows to return"),
    caller: str = Depends(require_api_key),
) -> dict:
    """Delivery log: every send attempt with its status and, for failures, the reason.

    Always requires an API key: the response contains every recipient address
    and phone number the service has handled.
    """
    if delivery_status and delivery_status not in ("sent", "failed"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="status must be 'sent' or 'failed'"
        )
    if channel and channel not in ("email", "sms"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="channel must be 'email' or 'sms'")

    from app.db import async_session_factory

    if async_session_factory is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Delivery log requires DATABASE_URL to be configured",
        )

    entries = await list_deliveries(
        status=delivery_status, channel=channel, recipient=recipient, limit=limit
    )
    return {"count": len(entries), "deliveries": entries}
