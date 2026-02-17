"""API routes: send document via email or SMS."""

import re
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.delivery import EmailDocumentDeliverer, SMSDocumentDeliverer
from app.services.email_service import EmailDeliveryError, EmailService
from app.services.signing_service import SigningError, SigningService
from app.services.sms_service import SMSDeliveryError, SMSService
from app.services.storage_service import StorageError, StorageService
from app.utils.audit import log_operation
from app.utils.logger import logger
from app.utils.validators import validate_email, validate_phone_number

router = APIRouter(tags=["documents"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pdf_attachment_filename(original_filename: str) -> str:
    """Normalize filename to .pdf extension (used for S3 key)."""
    base, _ = (original_filename.rsplit(".", 1) if "." in original_filename else (original_filename, ""))
    return f"{base}.pdf" if base else "document.pdf"


def _email_attachment_filename(business_name: str | None, original_filename: str) -> str:
    """Return the PDF attachment filename for emails.

    Priority:
    1. business_name  →  e.g. "יוסי פתרונות תוכנה.pdf"
    2. original_filename (unless empty / noname / unnamed)
    3. fallback: "document.pdf"
    """
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


def _build_email_body(business_name: str | None, client_name: str | None, body: str | None) -> str:
    """Compose the email body text."""
    body_text = (body or "").strip()
    business = (business_name or "").strip()
    client = (client_name or "").strip()

    if body_text and body_text.lower() != "none":
        if business and business not in body_text:
            return f'שלום רב!\n\nהקבלה מ{business} מצו"ב למייל\n\n{body_text}'
        return body_text

    if business:
        return f'שלום רב!\n\nהקבלה מ{business} מצו"ב למייל\n\nתודה'
    if client:
        return f'שלום רב!\n\nהקבלה מ{client} מצו"ב למייל\n\nתודה'
    return 'שלום רב!\n\nהקבלה מצו"ב למייל\n\nתודה'


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

_email_deliverer = EmailDocumentDeliverer()
_sms_deliverer = SMSDocumentDeliverer()
_signing_service: SigningService | None = None
_storage_service = StorageService()
_email_service = EmailService()
_sms_service = SMSService()


def _get_signing_service() -> SigningService:
    """Lazy-init SigningService so app can start without PRIVATE_KEY_* configured."""
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
    subject: str | None = Form(None, description="Email subject"),
    so: str | None = Form(None, description="(legacy) Email subject"),
    body: str | None = Form(None, description="Email body"),
    business_name: str | None = Form(None, description="Business name to show as sender name"),
    business_email: str | None = Form(None, description="Business email to also send document to"),
) -> dict:
    """Receive a document and send it via email as attachment."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File must have a filename")
    if not validate_email(email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid email address")
    if business_email and not validate_email(business_email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid business email address")

    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"Failed to read upload: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to read uploaded file") from e

    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    b_name = _sanitize(business_name)
    b_email = _sanitize(business_email)
    effective_subject = subject or so
    attachment_filename = _email_attachment_filename(b_name, file.filename)
    email_body = _build_email_body(b_name, client_name=None, body=body)

    logger.info(f"send-email: business_name='{b_name}', business_email='{b_email}', email='{email}'")
    logger.info(f"send-email: attachment_filename='{attachment_filename}'")

    try:
        await _email_service.send_document(
            to_email=email,
            document=content,
            filename=attachment_filename,
            subject=effective_subject or f"Document: {attachment_filename}",
            body=email_body,
            from_name=b_name,
            reply_to=b_email,
        )
        logger.info(f"Successfully sent email to client: {email}")
    except EmailDeliveryError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Email delivery failed: {e}") from e

    b_email_trimmed = b_email
    if b_email_trimmed:
        logger.info(f"Sending document copy to business email: {b_email_trimmed}")
        try:
            await _email_service.send_document(
                to_email=b_email_trimmed,
                document=content,
                filename=attachment_filename,
                subject=effective_subject or f"Document: {attachment_filename}",
                body=email_body,
                from_name=b_name,
                reply_to=b_email_trimmed,
            )
            logger.info(f"Successfully sent document copy to business email: {b_email_trimmed}")
        except EmailDeliveryError as e:
            logger.error(f"Failed to send document to business email {b_email_trimmed}: {e}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Email delivery failed (business copy): {e}") from e
    else:
        logger.warning("business_email not provided or empty, skipping business email copy")

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
    """Receive a document, upload to S3, and send SMS with download link."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File must have a filename")
    if not validate_phone_number(phone):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid phone number")

    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"Failed to read upload: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to read uploaded file") from e

    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    try:
        ok = await _sms_deliverer.deliver(
            document=content,
            filename=file.filename,
            recipient=phone,
            message=message,
        )
    except (StorageError, SMSDeliveryError) as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Delivery failed: {e}") from e

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
    client_name: str | None = Form(None, description="Client name for email body"),
    business_name: str | None = Form(None, description="Business name to include in email"),
    business_email: str | None = Form(None, description="Business email to also send document to"),
) -> dict:
    """Sign PDF, upload to S3, and send email with the signed document attached."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File must have a filename")
    if not validate_email(email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid email address")

    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"Failed to read upload: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to read uploaded file") from e

    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    b_name = _sanitize(business_name)
    b_email = _sanitize(business_email)
    effective_subject = subject or so

    # s3_filename uses the raw normalized name; attachment_filename uses business name
    s3_filename = _pdf_attachment_filename(file.filename)
    attachment_filename = _email_attachment_filename(b_name, file.filename)
    email_body = _build_email_body(b_name, client_name=client_name, body=body)

    logger.info(f"sign-and-email: business_name='{b_name}', business_email='{b_email}', email='{email}', so='{so}'")
    logger.info(f"sign-and-email: s3_filename='{s3_filename}', attachment_filename='{attachment_filename}'")

    try:
        signing_svc = _get_signing_service()
        signed_content, signature_data = signing_svc.sign_pdf(content)

        _storage_service.upload_file(
            content=signed_content,
            filename=s3_filename,
            content_type="application/pdf",
            metadata={
                "document-hash": signature_data["hash"],
                "document-signature": signature_data["signature"],
                "signature-algorithm": signature_data["algorithm"],
                "original-filename": file.filename,
                "signed-at": datetime.utcnow().isoformat(),
            },
        )
        download_url = _storage_service.generate_presigned_url(s3_filename)

        logger.info(f"Sending email to client: {email}, from_name: '{b_name}'")
        await _email_service.send_document(
            to_email=email,
            document=signed_content,
            filename=attachment_filename,
            subject=effective_subject or f"מסמך חתום: {attachment_filename}",
            body=email_body,
            from_name=b_name,
            reply_to=b_email,
        )
        logger.info(f"Successfully sent email to client: {email}")

        if b_email:
            if not validate_email(b_email):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid business email address")
            logger.info(f"Sending document copy to business email: {b_email}")
            try:
                await _email_service.send_document(
                    to_email=b_email,
                    document=signed_content,
                    filename=attachment_filename,
                    subject=effective_subject or f"מסמך חתום: {attachment_filename}",
                    body=email_body,
                    from_name=b_name,
                    reply_to=b_email,
                )
                logger.info(f"Successfully sent document copy to business email: {b_email}")
            except EmailDeliveryError as e:
                logger.error(f"Failed to send document to business email {b_email}: {e}")
                raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to send copy to business email: {e}") from e
        else:
            logger.warning("business_email not provided or empty (after sanitize), skipping business email copy")

        audit_metadata = {
            "s3_key": s3_filename,
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
            "s3_key": s3_filename,
            "download_url": download_url,
            "signature": {
                "hash": signature_data["hash"],
                "algorithm": signature_data["algorithm"],
            },
            **({"business_recipient": b_email} if b_email else {}),
        }

    except SigningError as e:
        logger.error(f"Signing error in sign-and-email: {e}", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Signing failed: {e}") from e
    except StorageError as e:
        logger.error(f"Storage error in sign-and-email: {e}", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"S3 upload failed: {e}") from e
    except EmailDeliveryError as e:
        logger.error(f"Email delivery error in sign-and-email: {e}", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Email delivery failed: {e}") from e
    except Exception as e:
        logger.error(f"Unexpected error in sign-and-email: {e}", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unexpected error: {e}") from e


@router.post("/documents/sign-and-sms", status_code=status.HTTP_200_OK)
async def sign_and_sms(
    file: UploadFile = File(..., description="PDF document to sign and send"),
    phone: str = Form(..., description="Recipient phone number"),
    message: str | None = Form(None, description="Optional SMS message"),
) -> dict:
    """Sign PDF, upload to S3, and send SMS with download link."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File must have a filename")
    if not validate_phone_number(phone):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid phone number")

    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"Failed to read upload: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to read uploaded file") from e

    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    try:
        signing_svc = _get_signing_service()
        signed_content, signature_data = signing_svc.sign_pdf(content)

        pdf_filename = _pdf_attachment_filename(file.filename)

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

    except SigningError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Signing failed: {e}") from e
    except StorageError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"S3 upload failed: {e}") from e
    except SMSDeliveryError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SMS delivery failed: {e}") from e


@router.post("/documents/verify-signature", status_code=status.HTTP_200_OK)
async def verify_document_signature(
    file: UploadFile = File(..., description="PDF document to verify"),
) -> dict:
    """Verify digital signature of a PDF document."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File must have a filename")

    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"Failed to read upload: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to read uploaded file") from e

    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    try:
        verification_result = _get_signing_service().verify_pdf_signature(content)
        return {"filename": file.filename, "verification": verification_result}
    except SigningError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Verification failed: {e}") from e