"""Integration tests for full invoice processing flow."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.invoice import Address, Customer, DigitalInvoice, InvoiceItem, Supplier
from app.services.email_service import EmailService
from app.services.invoice_builder import InvoiceBuilder
from app.services.invoice_parser import InvoiceParser
from app.services.pdf_reader import PDFReader
from app.services.sms_service import SMSService


class TestFullFlow:
    """Integration tests for complete invoice processing."""

    @patch("app.services.pdf_reader.pdfplumber")
    def test_pdf_to_digital_invoice(self, mock_pdfplumber):
        """Test full flow from PDF to digital invoice."""
        # Mock PDF reading
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = """
        Invoice #INV-001
        Date: 2024-01-15
        Supplier: Test Company Ltd
        Business ID: 123456789
        Address: 123 Main St, Tel Aviv, Israel
        Customer: Customer Name
        Item: Test Product
        Quantity: 1
        Price: 100.00 ILS
        VAT: 17.00 ILS
        Total: 117.00 ILS
        """
        mock_pdf.pages = [mock_page]
        mock_pdfplumber.open.return_value.__enter__.return_value = mock_pdf

        # Read PDF
        pdf_reader = PDFReader(ocr_enabled=False)
        text = pdf_reader.read_text(Path("test.pdf"))

        # Parse invoice
        parser = InvoiceParser()
        parsed_data = parser.parse(text)

        # Build invoice
        builder = InvoiceBuilder()
        invoice = builder.build(parsed_data)

        # Verify result
        assert isinstance(invoice, DigitalInvoice)
        assert invoice.invoice_number is not None
        assert invoice.supplier is not None
        assert len(invoice.items) > 0
        assert invoice.total_including_vat > 0

    @patch("app.core.signing.load_certificate")
    @patch("app.core.signing.load_private_key")
    @patch("app.core.signing.verify_certificate_chain")
    @patch("app.services.pdf_reader.pdfplumber")
    def test_full_flow_with_signing(
        self, mock_pdfplumber, mock_verify, mock_load_key, mock_load_cert
    ):
        """Test full flow including signing."""
        from app.core.signing import DigitalSigner
        from cryptography.hazmat.primitives.asymmetric import rsa

        # Mock PDF reading
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = """
        Invoice #INV-001
        Date: 2024-01-15
        Supplier: Test Company
        Business ID: 123456789
        Total: 117.00 ILS
        """
        mock_pdf.pages = [mock_page]
        mock_pdfplumber.open.return_value.__enter__.return_value = mock_pdf

        # Mock certificate and key
        mock_cert = MagicMock()
        mock_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        mock_load_cert.return_value = mock_cert
        mock_load_key.return_value = mock_key
        mock_verify.return_value = True

        # Process invoice
        pdf_reader = PDFReader(ocr_enabled=False)
        text = pdf_reader.read_text(Path("test.pdf"))

        parser = InvoiceParser()
        parsed_data = parser.parse(text)

        builder = InvoiceBuilder()
        invoice = builder.build(parsed_data)

        signer = DigitalSigner(
            cert_path=Path("test_cert.pem"),
            key_path=Path("test_key.pem"),
        )
        signer._cert = mock_cert
        signer._private_key = mock_key

        signed_invoice = signer.create_signed_invoice(invoice)

        # Verify signed document
        assert "invoice" in signed_invoice
        assert "signature" in signed_invoice
        assert "version" in signed_invoice

    @patch("app.services.email_service.httpx.AsyncClient")
    @patch("app.services.pdf_reader.pdfplumber")
    async def test_full_flow_with_email_delivery(self, mock_pdfplumber, mock_client):
        """Test full flow including email delivery."""
        # Mock PDF reading
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = """
        Invoice #INV-001
        Date: 2024-01-15
        Supplier: Test Company
        Business ID: 123456789
        Total: 117.00 ILS
        """
        mock_pdf.pages = [mock_page]
        mock_pdfplumber.open.return_value.__enter__.return_value = mock_pdf

        # Mock email API
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        # Process invoice
        pdf_reader = PDFReader(ocr_enabled=False)
        text = pdf_reader.read_text(Path("test.pdf"))

        parser = InvoiceParser()
        parsed_data = parser.parse(text)

        builder = InvoiceBuilder()
        invoice = builder.build(parsed_data)

        # Send email
        email_service = EmailService(
            provider="api",
            api_url="https://api.test.com",
            api_key="test_key",
        )

        invoice_data = {"invoice": invoice.to_json_dict()}
        result = await email_service.send_invoice(
            "test@example.com", invoice_data, invoice.invoice_number
        )

        assert result is True
