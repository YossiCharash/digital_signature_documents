"""Unit tests for invoice builder service."""

from decimal import Decimal

import pytest

from app.models.invoice import DigitalInvoice
from app.services.invoice_builder import InvoiceBuildError, InvoiceBuilder


class TestInvoiceBuilder:
    """Test cases for InvoiceBuilder."""

    def test_init(self):
        """Test InvoiceBuilder initialization."""
        builder = InvoiceBuilder()
        assert builder is not None

    def test_build_valid_invoice(self):
        """Test building valid invoice."""
        parsed_data = {
            "invoice_number": "INV-001",
            "issue_date": "2024-01-15",
            "supplier": {
                "name": "Test Supplier Ltd",
                "business_id": "123456789",
                "address": {
                    "street": "123 Main St",
                    "city": "Tel Aviv",
                    "postal_code": "12345",
                    "country": "Israel",
                },
            },
            "customer": {
                "name": "Test Customer",
                "business_id": None,
            },
            "line_items": [
                {
                    "description": "Test Item",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("100.00"),
                    "vat_rate": Decimal("17.00"),
                    "line_total": Decimal("100.00"),
                    "line_total_vat": Decimal("17.00"),
                    "line_total_with_vat": Decimal("117.00"),
                }
            ],
            "totals": {
                "total": Decimal("117.00"),
            },
        }

        builder = InvoiceBuilder()
        invoice = builder.build(parsed_data)

        assert isinstance(invoice, DigitalInvoice)
        assert invoice.invoice_number == "INV-001"
        assert invoice.supplier.name == "Test Supplier Ltd"
        assert len(invoice.items) == 1
        assert invoice.total_including_vat == Decimal("117.00")

    def test_build_missing_invoice_number(self):
        """Test building invoice with missing invoice number."""
        parsed_data = {
            "issue_date": "2024-01-15",
            "supplier": {"name": "Test", "business_id": "123456789"},
            "line_items": [],
        }

        builder = InvoiceBuilder()
        with pytest.raises(InvoiceBuildError):
            builder.build(parsed_data)

    def test_build_supplier(self):
        """Test supplier building."""
        builder = InvoiceBuilder()
        supplier_data = {
            "name": "Test Supplier",
            "business_id": "123456789",
            "address": {
                "street": "123 St",
                "city": "City",
                "country": "Israel",
            },
        }

        supplier = builder._build_supplier(supplier_data)
        assert supplier.name == "Test Supplier"
        assert supplier.business_id == "123456789"

    def test_build_customer(self):
        """Test customer building."""
        builder = InvoiceBuilder()
        customer_data = {
            "name": "Test Customer",
            "business_id": None,
        }

        customer = builder._build_customer(customer_data)
        assert customer.name == "Test Customer"
