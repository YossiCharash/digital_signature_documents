"""Unit tests for digital signing service."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.signing import DigitalSigner, SigningError
from app.models.invoice import Address, Customer, DigitalInvoice, InvoiceItem, Supplier


class TestDigitalSigner:
    """Test cases for DigitalSigner."""

    @patch("app.core.signing.load_certificate")
    @patch("app.core.signing.load_private_key")
    @patch("app.core.signing.verify_certificate_chain")
    def test_init(self, mock_verify, mock_load_key, mock_load_cert):
        """Test DigitalSigner initialization."""
        mock_cert = MagicMock()
        mock_key = MagicMock()
        mock_load_cert.return_value = mock_cert
        mock_load_key.return_value = mock_key
        mock_verify.return_value = True

        signer = DigitalSigner(
            cert_path=Path("test_cert.pem"),
            key_path=Path("test_key.pem"),
        )

        assert signer is not None
        mock_load_cert.assert_called_once()
        mock_load_key.assert_called_once()

    def test_create_test_invoice(self):
        """Create a test invoice for signing tests."""
        supplier = Supplier(
            name="Test Supplier",
            business_id="123456789",
            address=Address(street="123 St", city="Tel Aviv", country="Israel"),
        )
        customer = Customer(name="Test Customer", business_id=None)
        items = [
            InvoiceItem(
                description="Test Item",
                quantity=Decimal("1"),
                unit_price=Decimal("100.00"),
                vat_rate=Decimal("17.00"),
                total_excluding_vat=Decimal("100.00"),
                vat_amount=Decimal("17.00"),
                total_including_vat=Decimal("117.00"),
            )
        ]

        invoice = DigitalInvoice(
            invoice_number="INV-001",
            issue_date=date(2024, 1, 15),
            supplier=supplier,
            customer=customer,
            items=items,
            subtotal_excluding_vat=Decimal("100.00"),
            total_vat=Decimal("17.00"),
            total_including_vat=Decimal("117.00"),
        )

        return invoice

    @patch("app.core.signing.load_certificate")
    @patch("app.core.signing.load_private_key")
    @patch("app.core.signing.verify_certificate_chain")
    def test_sign_invoice(self, mock_verify, mock_load_key, mock_load_cert):
        """Test invoice signing."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa

        # Create mock certificate and key
        mock_cert = MagicMock()
        mock_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        mock_load_cert.return_value = mock_cert
        mock_load_key.return_value = mock_key
        mock_verify.return_value = True

        signer = DigitalSigner(
            cert_path=Path("test_cert.pem"),
            key_path=Path("test_key.pem"),
        )
        signer._cert = mock_cert
        signer._private_key = mock_key

        invoice = self.test_create_test_invoice()

        signature_data = signer.sign_invoice(invoice)

        assert "signature" in signature_data
        assert "algorithm" in signature_data
        assert signature_data["algorithm"] == "SHA256"

    @patch("app.core.signing.load_certificate")
    @patch("app.core.signing.load_private_key")
    @patch("app.core.signing.verify_certificate_chain")
    def test_create_signed_invoice(self, mock_verify, mock_load_key, mock_load_cert):
        """Test creating complete signed invoice document."""
        from cryptography.hazmat.primitives.asymmetric import rsa

        mock_cert = MagicMock()
        mock_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        mock_load_cert.return_value = mock_cert
        mock_load_key.return_value = mock_key
        mock_verify.return_value = True

        signer = DigitalSigner(
            cert_path=Path("test_cert.pem"),
            key_path=Path("test_key.pem"),
        )
        signer._cert = mock_cert
        signer._private_key = mock_key

        invoice = self.test_create_test_invoice()

        signed_doc = signer.create_signed_invoice(invoice)

        assert "invoice" in signed_doc
        assert "signature" in signed_doc
        assert "version" in signed_doc
        assert signed_doc["format"] == "digital_invoice_israel"
