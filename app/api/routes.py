"""API routes: send document via email or SMS."""

import re
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.services.email_service import EmailDeliveryError, EmailService
from app.services.signing_service import SigningError, SigningService
from app.services.sms_service import SMSDeliveryError, SMSService
from app.services.storage_service import StorageError, StorageService
from app.utils.audit import log_operation
from app.utils.logger import logger
from app.utils.validators import validate_email, validate_phone_number

router = APIRouter(tags=["documents"])

# Unicode RTL mark so plain-text email clients display Hebrew in correct order
_RTL_MARK = "\u200f"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _rtl_body(text: str) -> str:
    """Prefix with RTL mark so Hebrew text renders correctly in email clients."""
    return _RTL_MARK + text if text else text


def _email_attachment_filename(business_name: str | None, original_filename: str) -> str:
    """Return a clean PDF filename for the email attachment.

    Priority:
    1. Business name  →  e.g. "יוסי פתרונות תוכנה.pdf"
    2. Original filename (unless it is empty / "noname" / "unnamed")
    3. Fallback: "document.pdf"

    Ensures the recipient never sees the meaningless name "noname.pdf" that
    cloud platforms (e.g. Render) emit when no filename is sent.
    """
    if business_name and business_name.strip():
        # Strip characters that are unsafe in filenames (Windows + POSIX)
        safe = re.sub(r'[\\/:*?"<>|]', "_", business_name.strip()).strip()
        base = safe.rsplit(".", 1)[0] if "." in safe else safe
        return f"{base}.pdf" if base else "document.pdf"

    cleaned = (original_filename or "").strip()
    if not cleaned or cleaned.lower() in ("noname", "unnamed"):
        logger.debug("Attachment filename fallback: no business_name and filename is empty/noname")
        return "document.pdf"

    # Strip any existing extension and re-attach .pdf
    base = cleaned.rsplit(".", 1)[0] if "." in cleaned else cleaned
    result = f"{base}.pdf" if base else "document.pdf"

    if result.lower() == "noname.pdf":
        logger.debug("Attachment filename fallback: resolved to noname.pdf")
        return "document.pdf"

    return result


def _build_email_body(business_name: str | None, client_name: str | None, body: str | None) -> str:
    """Compose the email body, with an RTL prefix for Hebrew mail clients.

    Logic (in order):
    - If *body* is provided, use it (prepend business name header if needed).
    - Otherwise generate a short default greeting using business_name or client_name.
    """
    body_text = (body or "").strip()
    business = (business_name or "").strip()
    client = (client_name or "").strip()

    if body_text and body_text.lower() != "none":
        if business and business not in body_text:
            composed = f'שלום רב!\n\nהמסמך מ{business} מצו"ב למייל\n\n{body_text}'
        else:
            composed = body_text
    elif business:
        composed = f'שלום רב!\n\nהמסמך מ{business} מצו"ב למייל\n\nתודה'
    elif client:
        composed = f'שלום רב!\n\nהמסמך מ{client} מצו"ב למייל\n\nתודה'
    else:
        composed = 'שלום רב!\n\nהמסמך מצו"ב למייל\n\nתודה'

    return _rtl_body(composed)


def _sanitize(value: str | None) -> str | None:
    """Return None for blank / literal-'None' strings, otherwise return stripped value."""
    if value is None:
        return None
    stripped = str(value).strip()
    return None if stripped == "" or stripped.lower() == "none" else stripped


# ---------------------------------------------------------------------------
# Lazy-initialised services (avoids import-time side-effects)
# ---------------------------------------------------------------------------

_signing_service: SigningService | None = None
_storage_service = StorageService()
_email_service = EmailService()
_sms_service = SMSService()


def _get_signing_service() -> SigningService:
    """Lazily create SigningService so the app can start without PRIVATE_KEY_* configured."""
    global _signing_service
    if _signing_service is None:
        _signing_service = SigningService()
    return _signing_service


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/documents/send-email", status_code=status.HTTP_200_OK)
async def send_document_email(
        file: UploadFile = File(..., description="Document file to send"),
        email: str = Form(..., description="Recipient email"),
        subject: str | None = Form(None, alias="so", description="Email subject"),
        body: str | None = Form(None, description="Email body"),
        business_name: str | None = Form(None, description="Business name (used as sender name and attachment filename)"),
        business_email: str | None = Form(None, description="Business email to CC the document to"),
) -> dict:
    """Receive a document and send it as a PDF attachment via email."""
    _validate_upload(file)
    _validate_recipient_email(email)
    if business_email and not validate_email(business_email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid business email address")

    content = await _read_upload(file)

    b_name = _sanitize(business_name)
    b_email = _sanitize(business_email)
    attachment_filename = _email_attachment_filename(b_name, file.filename)
    email_body = _build_email_body(b_name, client_name=None, body=body)

    logger.info(f"send-email | to={email} business_name='{b_name}' business_email='{b_email}'")

    await _send_email(
        to_email=email,
        document=content,
        filename=attachment_filename,
        subject=subject or f"Document: {attachment_filename}",
        body=email_body,
        from_name=b_name,
        reply_to=b_email,
    )

    await _maybe_send_business_copy(
        b_email=b_email,
        recipient_email=email,
        document=content,
        filename=attachment_filename,
        subject=subject or f"Document: {attachment_filename}",
        body=email_body,
        from_name=b_name,
    )

    return {
        "status": "sent",
        "delivery": "email",
        "recipient": email,
        **({"business_recipient": b_email} if b_email else {}),
        "filename": attachment_filename,
    }


@router.post("/documents/send-sms", status_code=status.HTTP_200_OK)
async def send_document_sms(
        file: UploadFile = File(..., description="Document file to send"),
        phone: str = Form(..., description="Recipient phone number"),
        message: str | None = Form(None, description="Optional SMS message"),
) -> dict:
    """Receive a document, upload to S3, and send an SMS with a download link."""
    _validate_upload(file)
    if not validate_phone_number(phone):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid phone number")

    content = await _read_upload(file)

    try:
        ok = await _sms_service.deliver(
            document=content,
            filename=file.filename,
            recipient=phone,
            message=message,
        )
    except (StorageError, SMSDeliveryError) as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Delivery failed: {exc}") from exc

    if not ok:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="SMS delivery failed")

    return {
        "status": "sent",
        "delivery": "sms",
        "recipient": phone,
        "filename": file.filename,
    }


@router.post("/documents/sign-and-email", status_code=status.HTTP_200_OK)
async def sign_and_email(
        file: UploadFile = File(..., description="PDF document to sign and send"),
        email: str = Form(..., description="Recipient email"),
        subject: str | None = Form(None, description="Email subject"),
        so: str | None = Form(None, description="(legacy) Email subject"),
        body: str | None = Form(None, description="Email body"),
        client_name: str | None = Form(None, description="Client name for email greeting"),
        business_name: str | None = Form(None, description="Business name for email body and attachment filename"),
        business_email: str | None = Form(None, description="Business email to CC the document to"),
) -> dict:
    """Sign PDF, upload to S3, and send an email with the signed document attached."""
    _validate_upload(file)
    _validate_recipient_email(email)

    content = await _read_upload(file)

    b_name = _sanitize(business_name)
    b_email = _sanitize(business_email)
    effective_subject = subject or so
    attachment_filename = _email_attachment_filename(b_name, file.filename)
    email_body = _build_email_body(b_name, client_name=client_name, body=body)

    logger.info(
        f"sign-and-email | to={email} business_name='{b_name}' "
        f"business_email='{b_email}' subject='{effective_subject}'"
    )

    try:
        signed_content, signature_data = _get_signing_service().sign_pdf(content)

        s3_key = f"{file.filename}.pdf"
        _storage_service.upload_file(
            content=signed_content,
            filename=s3_key,
            content_type="application/pdf",
            metadata={
                "document-hash": signature_data["hash"],
                "document-signature": signature_data["signature"],
                "signature-algorithm": signature_data["algorithm"],
                "original-filename": file.filename,
                "signed-at": datetime.utcnow().isoformat(),
            },
        )
        download_url = _storage_service.generate_presigned_url(s3_key)

        final_subject = effective_subject or f"מסמך חתום: {attachment_filename}"

        await _send_email(
            to_email=email,
            document=signed_content,
            filename=attachment_filename,
            subject=final_subject,
            body=email_body,
            from_name=b_name,
            reply_to=b_email,
        )

        await _maybe_send_business_copy(
            b_email=b_email,
            recipient_email=email,
            document=signed_content,
            filename=attachment_filename,
            subject=final_subject,
            body=email_body,
            from_name=b_name,
        )

        audit_metadata = {
            "s3_key": s3_key,
            "signature": signature_data["signature"],
            **({"business_email": b_email} if b_email else {}),
            **({"business_name": b_name} if b_name else {}),
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
        }

    except SigningError as exc:
        logger.error(f"Signing error: {exc}", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Signing failed: {exc}") from exc
    except StorageError as exc:
        logger.error(f"Storage error: {exc}", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"S3 upload failed: {exc}") from exc
    except EmailDeliveryError as exc:
        logger.error(f"Email delivery error: {exc}", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Email delivery failed: {exc}") from exc
    except Exception as exc:
        logger.error(f"Unexpected error in sign-and-email: {exc}", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unexpected error: {exc}") from exc


@router.post("/documents/sign-and-sms", status_code=status.HTTP_200_OK)
async def sign_and_sms(
        file: UploadFile = File(..., description="PDF document to sign and send"),
        phone: str = Form(..., description="Recipient phone number"),
        message: str | None = Form(None, description="Optional SMS message"),
) -> dict:
    """Sign PDF, upload to S3, and send an SMS with a download link."""
    _validate_upload(file)
    if not validate_phone_number(phone):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid phone number")

    content = await _read_upload(file)

    try:
        signed_content, signature_data = _get_signing_service().sign_pdf(content)

        pdf_filename = f"{file.filename}.pdf"
        _storage_service.upload_file(
            content=signed_content,
            filename=pdf_filename,
            content_type="application/pdf",
            metadata={
                "document-hash": signature_data["hash"],
                "document-signature": signature_data["signature"],
                "signature-algorithm": signature_data["algorithm"],
                "original-filename": file.filename,
                "signed-at": datetime.utcnow().isoformat(),
            },
        )
        download_url = _storage_service.generate_presigned_url(pdf_filename)

        await _sms_service.send_document_link(
            to_phone=phone,
            document_url=download_url,
            message=message,
        )

        log_operation(
            operation="sign-and-sms",
            document_hash=signature_data["hash"],
            recipient=phone,
            filename=pdf_filename,
            metadata={"s3_key": pdf_filename, "signature": signature_data["signature"]},
        )

        return {
            "status": "signed_and_sent",
            "delivery": "sms",
            "recipient": phone,
            "filename": pdf_filename,
            "s3_key": pdf_filename,
            "download_url": download_url,
            "signature": {
                "hash": signature_data["hash"],
                "algorithm": signature_data["algorithm"],
            },
        }

    except SigningError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Signing failed: {exc}") from exc
    except StorageError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"S3 upload failed: {exc}") from exc
    except SMSDeliveryError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SMS delivery failed: {exc}") from exc


@router.post("/documents/verify-signature", status_code=status.HTTP_200_OK)
async def verify_document_signature(
        file: UploadFile = File(..., description="PDF document to verify"),
) -> dict:
    """Verify the digital signature embedded in a PDF document."""
    _validate_upload(file)
    content = await _read_upload(file)

    try:
        verification_result = _get_signing_service().verify_pdf_signature(content)
        return {"filename": file.filename, "verification": verification_result}
    except SigningError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Verification failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Shared private helpers
# ---------------------------------------------------------------------------

def _validate_upload(file: UploadFile) -> None:
    """Raise 400 if the uploaded file has no filename."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File must have a filename")


def _validate_recipient_email(email: str) -> None:
    """Raise 400 if the email address is invalid."""
    if not validate_email(email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid email address")


async def _read_upload(file: UploadFile) -> bytes:
    """Read file bytes, raising appropriate HTTP errors on failure."""
    try:
        content = await file.read()
    except Exception as exc:
        logger.error(f"Failed to read upload: {exc}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to read uploaded file") from exc

    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    return content


async def _send_email(
        to_email: str,
        document: bytes,
        filename: str,
        subject: str,
        body: str,
        from_name: str | None,
        reply_to: str | None,
) -> None:
    """Send a single document email, translating EmailDeliveryError to HTTP 500."""
    logger.info(f"Sending email | to={to_email} filename='{filename}' from_name='{from_name}'")
    try:
        await _email_service.send_document(
            to_email=to_email,
            document=document,
            filename=filename,
            subject=subject,
            body=body,
            from_name=from_name,
            reply_to=reply_to,
        )
        logger.info(f"Email sent successfully | to={to_email}")
    except EmailDeliveryError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Email delivery failed: {exc}") from exc


async def _maybe_send_business_copy(
        b_email: str | None,
        recipient_email: str,
        document: bytes,
        filename: str,
        subject: str,
        body: str,
        from_name: str | None,
) -> None:
    """Send a copy to the business email, unless it is missing or identical to the recipient."""
    if not b_email:
        logger.warning("business_email not provided — skipping business copy")
        return

    if b_email.lower() == recipient_email.strip().lower():
        logger.info(f"business_email matches recipient ({recipient_email}) — skipping duplicate send")
        return

    if not validate_email(b_email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid business email address")

    logger.info(f"Sending document copy to business email: {b_email}")
    try:
        await _email_service.send_document(
            to_email=b_email,
            document=document,
            filename=filename,
            subject=subject,
            body=body,
            from_name=from_name,
            reply_to=b_email,
        )
        logger.info(f"Business copy sent successfully | to={b_email}")
    except EmailDeliveryError as exc:
        logger.error(f"Failed to send business copy to {b_email}: {exc}")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Email delivery failed (business copy): {exc}",
        ) from exc