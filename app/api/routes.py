"""API routes for invoice digitalization."""

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from app.core.signing import DigitalSigner, SigningError
from app.models.invoice import DigitalInvoice
from app.services.email_service import EmailDeliveryError, EmailService
from app.services.invoice_builder import InvoiceBuildError, InvoiceBuilder
from app.services.invoice_parser import InvoiceParseError, InvoiceParser
from app.services.pdf_reader import ImageOnlyPDFError, PDFReadError, PDFReader
from app.services.sms_service import SMSDeliveryError, SMSService
from app.utils.logger import logger

router = APIRouter(tags=["invoices"])

# Initialize services
pdf_reader = PDFReader()
invoice_parser = InvoiceParser()
invoice_builder = InvoiceBuilder()
digital_signer = DigitalSigner()
email_service = EmailService()
sms_service = SMSService()


@router.post("/invoices/upload", status_code=status.HTTP_201_CREATED)
async def upload_invoice(
    file: UploadFile = File(..., description="Invoice PDF file"),
) -> dict:
    """Upload and process an invoice PDF."""
    try:
        logger.info(f"Received invoice upload: {file.filename}")

        # Validate file type
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must be a PDF",
            )

        # Read PDF bytes
        pdf_bytes = await file.read()
        if len(pdf_bytes) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty",
            )

        # Extract text from PDF (text-based only)
        try:
            text = pdf_reader.read_bytes(pdf_bytes)
        except ImageOnlyPDFError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(e),
            ) from e
        except PDFReadError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to read PDF: {str(e)}",
            ) from e

        # Parse invoice data
        try:
            parsed_data = invoice_parser.parse(text)
        except InvoiceParseError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to parse invoice: {str(e)}",
            ) from e

        # Build digital invoice
        try:
            invoice = invoice_builder.build(parsed_data)
        except InvoiceBuildError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to build invoice: {str(e)}",
            ) from e

        # Sign invoice
        try:
            signed_invoice = digital_signer.create_signed_invoice(invoice)
        except SigningError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to sign invoice: {str(e)}",
            ) from e

        logger.info(f"Invoice {invoice.invoice_number} processed successfully")

        return {
            "invoice_id": str(invoice.invoice_id),
            "invoice_number": invoice.invoice_number,
            "status": "processed",
            "signed_invoice": signed_invoice,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing invoice: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e


@router.post("/invoices/send", status_code=status.HTTP_200_OK)
async def send_invoice(
    invoice_id: str = Form(..., description="Invoice ID"),
    delivery_method: str = Form(..., description="Delivery method: email or sms"),
    recipient: str = Form(..., description="Email address or phone number"),
    subject: Optional[str] = Form(None, description="Email subject (for email only)"),
    message: Optional[str] = Form(None, description="Custom message"),
) -> dict:
    """Send a processed invoice via email or SMS."""
    try:
        logger.info(f"Sending invoice {invoice_id} via {delivery_method} to {recipient}")

        # In production, retrieve invoice from storage/database
        # For now, this is a placeholder that expects the invoice to be stored
        # after upload. In a real system, you would:
        # 1. Store signed invoices in a database or file system
        # 2. Retrieve by invoice_id
        # 3. Send the stored signed invoice

        # This endpoint assumes the invoice was previously uploaded and stored
        # You would implement storage logic in the upload endpoint

        if delivery_method.lower() == "email":
            # For email, we need the full signed invoice
            # In production, retrieve from storage
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Email delivery requires invoice storage implementation",
            )

        elif delivery_method.lower() == "sms":
            # For SMS, we only send a reference
            try:
                success = await sms_service.send_invoice_reference(
                    to_phone=recipient,
                    invoice_number=invoice_id,  # In production, get actual invoice number
                    invoice_id=invoice_id,
                    message=message,
                )
                if not success:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="SMS delivery failed",
                    )

                return {
                    "invoice_id": invoice_id,
                    "delivery_method": "sms",
                    "recipient": recipient,
                    "status": "sent",
                }

            except SMSDeliveryError as e:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"SMS delivery failed: {str(e)}",
                ) from e

        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Delivery method must be 'email' or 'sms'",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error sending invoice: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e


@router.get("/invoices/{invoice_id}", status_code=status.HTTP_200_OK)
async def get_invoice(invoice_id: str) -> dict:
    """Retrieve a processed invoice by ID."""
    # In production, retrieve from storage/database
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Invoice retrieval requires storage implementation",
    )
