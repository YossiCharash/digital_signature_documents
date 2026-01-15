"""Unit tests for invoice parser service."""

import pytest

from app.services.invoice_parser import InvoiceParseError, InvoiceParser


class TestInvoiceParser:
    """Test cases for InvoiceParser."""

    def test_init(self):
        """Test InvoiceParser initialization."""
        parser = InvoiceParser()
        assert parser is not None

    def test_parse_minimal_invoice(self):
        """Test parsing minimal valid invoice."""
        text = """
        Invoice #INV-001
        Date: 2024-01-15
        Supplier: Test Company Ltd
        Business ID: 123456789
        Customer: Customer Name
        Total: 100.00 ILS
        """
        parser = InvoiceParser()
        result = parser.parse(text)

        assert result["invoice_number"] is not None
        assert result["issue_date"] is not None
        assert result["supplier"] is not None
        assert len(result["line_items"]) > 0

    def test_parse_missing_invoice_number(self):
        """Test parsing invoice without invoice number."""
        text = """
        Date: 2024-01-15
        Supplier: Test Company
        Total: 100.00
        """
        parser = InvoiceParser()
        with pytest.raises(InvoiceParseError, match="Invoice number"):
            parser.parse(text)

    def test_parse_missing_date(self):
        """Test parsing invoice without date."""
        text = """
        Invoice #INV-001
        Supplier: Test Company
        Total: 100.00
        """
        parser = InvoiceParser()
        with pytest.raises(InvoiceParseError, match="Issue date"):
            parser.parse(text)

    def test_extract_invoice_number(self):
        """Test invoice number extraction."""
        parser = InvoiceParser()
        text = "Invoice #INV-12345"
        invoice_num = parser._extract_invoice_number(text)
        assert invoice_num == "INV-12345"

    def test_extract_date(self):
        """Test date extraction."""
        parser = InvoiceParser()
        text = "Date: 2024-01-15"
        date_str = parser._extract_date(text)
        assert date_str == "2024-01-15"

    def test_extract_supplier(self):
        """Test supplier extraction."""
        parser = InvoiceParser()
        text = "Supplier: Test Company Ltd Business ID: 123456789"
        supplier = parser._extract_supplier(text)
        assert supplier is not None
        assert "name" in supplier

    def test_parse_decimal(self):
        """Test decimal parsing."""
        parser = InvoiceParser()
        assert parser._parse_decimal("100.50") == 100.50
        assert parser._parse_decimal("100,50") == 100.50
        assert parser._parse_decimal("₪100.50") == 100.50
