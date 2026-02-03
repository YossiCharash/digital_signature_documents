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


def _pdf_attachment_filename(original_filename: str) -> str:
    """Normalize filename so PDF attachment has extension .pdf (for email and S3)."""
    base, _ = (
        original_filename.rsplit(".", 1) if "." in original_filename else (original_filename, "")
    )
    return f"{base}.pdf" if base else "document.pdf"


def _email_attachment_filename(business_name: str | None, original_filename: str) -> str:
    """Use business name as attachment filename with .pdf when provided, else normalized file name."""
    if business_name:
        # Keep letters (incl. Hebrew), digits, spaces, dots, hyphens; replace path/shell-unsafe chars
        safe = re.sub(r'[\\/:*?"<>|]', "_", business_name.strip()).strip()
        if safe:
            base = safe.rsplit(".", 1)[0] if "." in safe else safe
            return f"{base}.pdf" if base else "document.pdf"
    return _pdf_attachment_filename(original_filename)


def _attachment_name_source(business_name: str | None, body: str | None) -> str | None:
    """Get business name from explicit field or first non-empty body line."""
    if business_name and business_name.strip():
        return business_name.strip()
    if body and body.strip():
        for line in body.splitlines():
            candidate = line.strip()
            if candidate:
                return candidate
    return None


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must have a filename",
        )
    if not validate_email(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address",
        )
    if business_email and not validate_email(business_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid business email address",
        )

    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"Failed to read upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read uploaded file",
        ) from e

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    effective_subject = subject or so

    # Prepare email body with business name - always include business name if provided
    business_name_text = (business_name or "").strip()

    # Log received parameters for debugging
    logger.info(
        f"send-email: business_name='{business_name}', business_email='{business_email}', email='{email}'"
    )

    if body and body.strip():
        # If body is provided, check if business name is already in it
        # If not, prepend it to make it clear who sent it
        if business_name_text and business_name_text not in body:
            email_body = f'שלום רב!\n\nהקבלה מ{business_name_text} מצו"ב למייל\n\n{body}'
        else:
            email_body = body
    else:
        # Create default body with business name
        if business_name_text:
            email_body = f'שלום רב!\n\nהקבלה מ{business_name_text} מצו"ב למייל\n\nתודה'
        else:
            email_body = body or 'שלום רב!\n\nהקבלה מצו"ב למייל\n\nתודה'

    # Use business name from field or body as attachment filename (with .PDF) when provided
    attachment_name_source = _attachment_name_source(business_name, body)
    pdf_filename = _email_attachment_filename(attachment_name_source, file.filename)

    # Send email to client - use business_name as from_name
    logger.info(f"Sending email to client: {email}, from_name: '{business_name}'")
    try:
        await _email_service.send_document(
            to_email=email,
            document=content,
            filename=pdf_filename,
            subject=effective_subject or f"Document: {pdf_filename}",
            body=email_body,
            from_name=business_name,  # This will be the sender name in the email
            reply_to=business_email,
        )
        logger.info(f"Successfully sent email to client: {email}")
    except EmailDeliveryError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Email delivery failed: {str(e)}",
        ) from e

    # Also send to business email (if provided)
    business_email_trimmed = (business_email or "").strip() if business_email else None
    if business_email_trimmed:
        if not validate_email(business_email_trimmed):
            logger.error(f"Invalid business email address: {business_email_trimmed}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid business email address",
            )
        logger.info(f"Sending document copy to business email: {business_email_trimmed}")
        try:
            await _email_service.send_document(
                to_email=business_email_trimmed,
                document=content,
                filename=pdf_filename,
                subject=effective_subject or f"Document: {pdf_filename}",
                body=email_body,
                from_name=business_name,
                reply_to=business_email_trimmed,
            )
            logger.info(
                f"Successfully sent document copy to business email: {business_email_trimmed}"
            )
        except EmailDeliveryError as e:
            logger.error(f"Failed to send document to business email {business_email_trimmed}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Email delivery failed (business copy): {str(e)}",
            ) from e
    else:
        logger.warning("business_email not provided or empty, skipping business email copy")

    return {
        "status": "sent",
        "delivery": "email",
        "recipient": email,
        **({"business_recipient": business_email} if business_email else {}),
        "filename": pdf_filename,
    }


@router.post("/documents/send-sms", status_code=status.HTTP_200_OK)
async def send_document_sms(
    file: UploadFile = File(..., description="Document file to send"),
    phone: str = Form(..., description="Recipient phone number"),
    message: str | None = Form(None, description="Optional SMS message"),
) -> dict:
    """Receive a document, upload to S3, and send SMS with download link."""
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must have a filename",
        )
    if not validate_phone_number(phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone number",
        )

    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"Failed to read upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read uploaded file",
        ) from e

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    try:
        ok = await _sms_deliverer.deliver(
            document=content,
            filename=file.filename,
            recipient=phone,
            message=message,
        )
    except (StorageError, SMSDeliveryError) as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Delivery failed: {str(e)}",
        ) from e

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SMS delivery failed",
        )

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
    """Sign PDF, upload to S3, and send email with download link."""
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must have a filename",
        )
    if not validate_email(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address",
        )

    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"Failed to read upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read uploaded file",
        ) from e

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    try:
        signing_svc = _get_signing_service()
        # Sign PDF and get signed PDF bytes with embedded signature
        signed_content, signature_data = signing_svc.sign_pdf(content)

        # S3 key: normalized from file name (stable for storage/URLs)
        s3_filename = _pdf_attachment_filename(file.filename)

        # Upload signed PDF to S3 with metadata
        metadata = {
            "document-hash": signature_data["hash"],
            "document-signature": signature_data["signature"],
            "signature-algorithm": signature_data["algorithm"],
            "original-filename": file.filename,
            "signed-at": datetime.utcnow().isoformat(),
        }
        _storage_service.upload_file(
            content=signed_content,  # Upload signed PDF, not original
            filename=s3_filename,
            content_type="application/pdf",
            metadata=metadata,
        )

        # Generate pre-signed URL (for API response, not included in email)
        download_url = _storage_service.generate_presigned_url(s3_filename)

        effective_subject = subject or so

        # FIX: Handle cases where parameters might come as string 'None' or are missing
        def sanitize_param(val):
            if val is None or str(val).lower() == "none" or str(val).strip() == "":
                return None
            return str(val).strip()

        b_name = sanitize_param(business_name)
        b_email = sanitize_param(business_email)

        # Email attachment filename: business name from field or body with .PDF when provided
        attachment_name_source = _attachment_name_source(b_name, body)
        attachment_filename = _email_attachment_filename(attachment_name_source, file.filename)

        # Prepare email body with business name - always include business name if provided
        business_name_text = b_name or ""
        client_name_text = (client_name or "").strip()

        # Log received parameters for debugging
        logger.info(
            f"sign-and-email: business_name='{b_name}', business_email='{b_email}', email='{email}', so='{so}'"
        )

        if body and body.strip() and str(body).lower() != "none":
            # If body is provided, check if business name is already in it
            if business_name_text and business_name_text not in body:
                email_body = f'שלום רב!\n\nהקבלה מ{business_name_text} מצו"ב למייל\n\n{body}'
            else:
                email_body = body
        else:
            if business_name_text:
                email_body = f'שלום רב!\n\nהקבלה מ{business_name_text} מצו"ב למייל\n\nתודה'
            elif client_name_text:
                email_body = f'שלום רב!\n\nהקבלה מ{client_name_text} מצו"ב למייל\n\nתודה'
            else:
                email_body = 'שלום רב!\n\nהקבלה מצו"ב למייל\n\nתודה'

        # Send email to client - use business_name as from_name
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

        # Send email to business if business_email is provided
        if b_email:
            if not validate_email(b_email):
                logger.error(f"Invalid business email address: {b_email}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid business email address",
                )
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
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to send copy to business email: {str(e)}",
                ) from e
        else:
            logger.warning(
                "business_email not provided or empty (after sanitize), skipping business email copy"
            )

        # Audit log
        metadata = {
            "s3_key": s3_filename,
            "signature": signature_data["signature"],
        }
        if business_email:
            metadata["business_email"] = business_email
        if business_name:
            metadata["business_name"] = business_name
        log_operation(
            operation="sign-and-email",
            document_hash=signature_data["hash"],
            recipient=email,
            filename=attachment_filename,
            metadata=metadata,
        )

        response = {
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
        }
        if business_email:
            response["business_recipient"] = business_email
        return response
    except SigningError as e:
        logger.error(f"Signing error in sign-and-email: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Signing failed: {str(e)}",
        ) from e
    except StorageError as e:
        logger.error(f"Storage error in sign-and-email: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"S3 upload failed: {str(e)}",
        ) from e
    except EmailDeliveryError as e:
        logger.error(f"Email delivery error in sign-and-email: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Email delivery failed: {str(e)}",
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error in sign-and-email: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}",
        ) from e


@router.post("/documents/sign-and-sms", status_code=status.HTTP_200_OK)
async def sign_and_sms(
    file: UploadFile = File(..., description="PDF document to sign and send"),
    phone: str = Form(..., description="Recipient phone number"),
    message: str | None = Form(None, description="Optional SMS message"),
) -> dict:
    """Sign PDF, upload to S3, and send SMS with download link."""
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must have a filename",
        )
    if not validate_phone_number(phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone number",
        )

    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"Failed to read upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read uploaded file",
        ) from e

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    try:
        signing_svc = _get_signing_service()
        # Sign PDF and get signed PDF bytes with embedded signature
        signed_content, signature_data = signing_svc.sign_pdf(content)

        # Use normalized filename with .PDF extension for S3
        pdf_filename = _pdf_attachment_filename(file.filename)
        s3_filename = pdf_filename

        # Upload signed PDF to S3 with metadata
        metadata = {
            "document-hash": signature_data["hash"],
            "document-signature": signature_data["signature"],
            "signature-algorithm": signature_data["algorithm"],
            "original-filename": file.filename,
            "signed-at": datetime.utcnow().isoformat(),
        }
        _storage_service.upload_file(
            content=signed_content,  # Upload signed PDF, not original
            filename=s3_filename,
            content_type="application/pdf",
            metadata=metadata,
        )

        # Generate pre-signed URL
        download_url = _storage_service.generate_presigned_url(s3_filename)

        # Send SMS with link
        await _sms_service.send_document_link(
            to_phone=phone,
            document_url=download_url,
            message=message,
        )

        # Audit log
        log_operation(
            operation="sign-and-sms",
            document_hash=signature_data["hash"],
            recipient=phone,
            filename=pdf_filename,
            metadata={"s3_key": s3_filename, "signature": signature_data["signature"]},
        )

        return {
            "status": "signed_and_sent",
            "delivery": "sms",
            "recipient": phone,
            "filename": pdf_filename,
            "s3_key": s3_filename,
            "download_url": download_url,
            "signature": {
                "hash": signature_data["hash"],
                "algorithm": signature_data["algorithm"],
            },
        }
    except SigningError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Signing failed: {str(e)}",
        ) from e
    except StorageError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"S3 upload failed: {str(e)}",
        ) from e
    except SMSDeliveryError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SMS delivery failed: {str(e)}",
        ) from e


@router.post("/documents/verify-signature", status_code=status.HTTP_200_OK)
async def verify_document_signature(
    file: UploadFile = File(..., description="PDF document to verify"),
) -> dict:
    """Verify digital signature of a PDF document."""
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must have a filename",
        )

    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"Failed to read upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read uploaded file",
        ) from e

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    try:
        signing_svc = _get_signing_service()
        verification_result = signing_svc.verify_pdf_signature(content)

        return {
            "filename": file.filename,
            "verification": verification_result,
        }
    except SigningError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Verification failed: {str(e)}",
        ) from e
