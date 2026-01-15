"""Invoice parsing service to extract structured data from PDF text."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from app.models.invoice import Address, Customer, LineItem, Supplier
from app.utils.logger import logger
from app.utils.validators import validate_israeli_business_id


class InvoiceParseError(Exception):
    """Raised when invoice parsing fails."""

    pass


class InvoiceParser:
    """Service for parsing invoice data from extracted text."""

    def __init__(self):
        """Initialize invoice parser."""
        self.business_id_pattern = re.compile(r"\b\d{9}\b")
        self.date_patterns = [
            re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})"),
            re.compile(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})"),
        ]
        self.currency_pattern = re.compile(r"([₪$€£]|ILS|USD|EUR|GBP)")
        self.number_pattern = re.compile(r"\d+[.,]?\d*")

    def parse(self, text: str) -> Dict:
        """Parse invoice text and return structured data."""
        try:
            logger.info("Parsing invoice text")

            # Normalize text
            normalized_text = self._normalize_text(text)

            # Extract components
            invoice_number = self._extract_invoice_number(normalized_text)
            issue_date = self._extract_date(normalized_text)
            supplier = self._extract_supplier(normalized_text)
            customer = self._extract_customer(normalized_text)
            line_items = self._extract_line_items(normalized_text)
            totals = self._extract_totals(normalized_text)

            # Validate required fields
            if not invoice_number:
                raise InvoiceParseError("Invoice number is required")
            if not issue_date:
                raise InvoiceParseError("Issue date is required")
            if not supplier:
                raise InvoiceParseError("Supplier information is required")
            if not line_items:
                raise InvoiceParseError("At least one line item is required")

            # Build result
            result = {
                "invoice_number": invoice_number,
                "issue_date": issue_date,
                "supplier": supplier,
                "customer": customer,
                "line_items": line_items,
                "totals": totals,
            }

            logger.info(f"Successfully parsed invoice {invoice_number}")
            return result

        except Exception as e:
            logger.error(f"Invoice parsing failed: {e}")
            raise InvoiceParseError(f"Parsing failed: {e}") from e

    def _normalize_text(self, text: str) -> str:
        """Normalize text for parsing."""
        # Replace multiple spaces with single space
        text = re.sub(r"\s+", " ", text)
        # Remove special characters but keep numbers, letters, and common separators
        text = re.sub(r"[^\w\s\d.,;:()\-/₪$€£]", " ", text)
        return text.strip()

    def _extract_invoice_number(self, text: str) -> Optional[str]:
        """Extract invoice number."""
        patterns = [
            re.compile(r"(?:invoice|inv|מספר|חשבונית)[\s:]*#?[\s:]*([A-Z0-9\-]+)", re.IGNORECASE),
            re.compile(r"#[\s:]*([A-Z0-9\-]+)", re.IGNORECASE),
            re.compile(r"(?:מספר\s+חשבונית|invoice\s+number)[\s:]*([A-Z0-9\-]+)", re.IGNORECASE),
        ]

        for pattern in patterns:
            match = pattern.search(text)
            if match:
                invoice_num = match.group(1).strip()
                if len(invoice_num) >= 3:
                    return invoice_num

        # Fallback: look for alphanumeric codes
        fallback = re.search(r"\b([A-Z]{2,}\d{3,}|\d{6,})\b", text)
        if fallback:
            return fallback.group(1)

        return None

    def _extract_date(self, text: str) -> Optional[str]:
        """Extract issue date."""
        for pattern in self.date_patterns:
            matches = pattern.findall(text)
            for match in matches:
                try:
                    if len(match[2]) == 2:
                        year = 2000 + int(match[2])
                    else:
                        year = int(match[2])

                    if "/" in text or "-" in text:
                        # Try DD/MM/YYYY or YYYY-MM-DD
                        if year > 2000:
                            date_str = f"{year}-{int(match[1]):02d}-{int(match[0]):02d}"
                        else:
                            date_str = f"{year}-{int(match[0]):02d}-{int(match[1]):02d}"
                    else:
                        date_str = f"{year}-{int(match[1]):02d}-{int(match[0]):02d}"

                    # Validate date
                    datetime.strptime(date_str, "%Y-%m-%d")
                    return date_str
                except (ValueError, IndexError):
                    continue

        return None

    def _extract_supplier(self, text: str) -> Optional[Dict]:
        """Extract supplier information."""
        # Look for supplier section
        supplier_patterns = [
            re.compile(r"(?:supplier|vendor|ספק)[\s:]*([^\n]+)", re.IGNORECASE),
            re.compile(r"(?:from|מ)[\s:]*([^\n]+)", re.IGNORECASE),
        ]

        supplier_text = ""
        for pattern in supplier_patterns:
            match = pattern.search(text)
            if match:
                supplier_text = match.group(1)
                break

        if not supplier_text:
            # Try to find business ID and infer supplier
            business_ids = self.business_id_pattern.findall(text)
            if business_ids:
                supplier_text = text[: text.find(business_ids[0]) + 50]

        if not supplier_text:
            return None

        # Extract business ID
        business_id = None
        ids = self.business_id_pattern.findall(supplier_text)
        if ids:
            for bid in ids:
                if validate_israeli_business_id(bid):
                    business_id = bid
                    break

        # Extract name (text before business ID)
        name = supplier_text.split(business_id)[0].strip() if business_id else supplier_text[:50]
        name = re.sub(r"\s+", " ", name).strip()

        if not name or len(name) < 2:
            return None

        # Extract address (simplified)
        address = self._extract_address(text, "supplier")

        return {
            "name": name,
            "business_id": business_id or "",
            "address": address,
        }

    def _extract_customer(self, text: str) -> Optional[Dict]:
        """Extract customer information."""
        customer_patterns = [
            re.compile(r"(?:customer|client|buyer|לקוח)[\s:]*([^\n]+)", re.IGNORECASE),
            re.compile(r"(?:to|ל)[\s:]*([^\n]+)", re.IGNORECASE),
            re.compile(r"(?:bill\s+to|חשבון\s+ל)[\s:]*([^\n]+)", re.IGNORECASE),
        ]

        customer_text = ""
        for pattern in customer_patterns:
            match = pattern.search(text)
            if match:
                customer_text = match.group(1)
                break

        if not customer_text:
            return {
                "name": "Unknown Customer",
                "business_id": None,
                "address": None,
            }

        # Extract business ID
        business_id = None
        ids = self.business_id_pattern.findall(customer_text)
        if ids:
            for bid in ids:
                if validate_israeli_business_id(bid):
                    business_id = bid
                    break

        # Extract name
        name = customer_text.split(business_id)[0].strip() if business_id else customer_text[:50]
        name = re.sub(r"\s+", " ", name).strip()

        if not name:
            name = "Unknown Customer"

        address = self._extract_address(text, "customer")

        return {
            "name": name,
            "business_id": business_id,
            "address": address,
        }

    def _extract_address(self, text: str, entity: str) -> Optional[Dict]:
        """Extract address information (simplified)."""
        # This is a simplified address extraction
        # In production, use more sophisticated NLP or structured extraction
        address_pattern = re.compile(
            r"(?:address|כתובת)[\s:]*([^\n]{20,100})", re.IGNORECASE
        )
        match = address_pattern.search(text)
        if match:
            addr_text = match.group(1)
            # Try to extract city
            city_match = re.search(r"([A-Za-zא-ת]{2,})", addr_text)
            city = city_match.group(1) if city_match else "Unknown"
            return {
                "street": addr_text[:50],
                "city": city,
                "postal_code": None,
                "country": "Israel",
            }
        return None

    def _extract_line_items(self, text: str) -> List[Dict]:
        """Extract line items from invoice."""
        line_items = []

        # Look for table-like structures
        # Pattern: description, quantity, price, total
        item_patterns = [
            re.compile(
                r"([A-Za-zא-ת0-9\s]{10,})\s+(\d+[.,]?\d*)\s+([₪$€£]?\d+[.,]?\d*)\s+([₪$€£]?\d+[.,]?\d*)",
                re.IGNORECASE,
            ),
        ]

        for pattern in item_patterns:
            matches = pattern.finditer(text)
            for match in matches:
                try:
                    description = match.group(1).strip()
                    quantity = self._parse_decimal(match.group(2))
                    unit_price = self._parse_decimal(match.group(3))
                    line_total = self._parse_decimal(match.group(4))

                    if quantity > 0 and unit_price >= 0:
                        # Assume standard VAT rate (17% in Israel)
                        vat_rate = Decimal("17.00")
                        line_total_excl_vat = line_total / (Decimal("1") + vat_rate / Decimal("100"))
                        line_total_vat = line_total - line_total_excl_vat

                        line_items.append(
                            {
                                "description": description[:200],
                                "quantity": quantity,
                                "unit_price": unit_price,
                                "vat_rate": vat_rate,
                                "line_total": line_total_excl_vat,
                                "line_total_vat": line_total_vat,
                                "line_total_with_vat": line_total,
                            }
                        )
                except (ValueError, InvalidOperation):
                    continue

        # If no items found, create a single item from totals
        if not line_items:
            totals = self._extract_totals(text)
            if totals.get("total"):
                line_items.append(
                    {
                        "description": "Invoice Total",
                        "quantity": Decimal("1"),
                        "unit_price": totals["total"],
                        "vat_rate": Decimal("17.00"),
                        "line_total": totals.get("subtotal", totals["total"]),
                        "line_total_vat": totals.get("vat", Decimal("0")),
                        "line_total_with_vat": totals["total"],
                    }
                )

        return line_items

    def _extract_totals(self, text: str) -> Dict:
        """Extract totals from invoice."""
        totals = {}

        # Look for total patterns
        total_patterns = [
            re.compile(r"(?:total|סה\"כ|סכום)[\s:]*[₪$€£]?[\s:]*(\d+[.,]?\d*)", re.IGNORECASE),
            re.compile(r"(?:amount\s+due|לתשלום)[\s:]*[₪$€£]?[\s:]*(\d+[.,]?\d*)", re.IGNORECASE),
        ]

        for pattern in total_patterns:
            match = pattern.search(text)
            if match:
                total = self._parse_decimal(match.group(1))
                totals["total"] = total
                break

        # Look for subtotal
        subtotal_pattern = re.compile(
            r"(?:subtotal|sub-total|סיכום\s+ביניים)[\s:]*[₪$€£]?[\s:]*(\d+[.,]?\d*)",
            re.IGNORECASE,
        )
        match = subtotal_pattern.search(text)
        if match:
            totals["subtotal"] = self._parse_decimal(match.group(1))

        # Look for VAT
        vat_pattern = re.compile(
            r"(?:vat|מע\"מ|tax)[\s:]*[₪$€£]?[\s:]*(\d+[.,]?\d*)", re.IGNORECASE
        )
        match = vat_pattern.search(text)
        if match:
            totals["vat"] = self._parse_decimal(match.group(1))

        return totals

    def _parse_decimal(self, value: str) -> Decimal:
        """Parse decimal value, handling both comma and dot separators."""
        # Replace comma with dot
        normalized = value.replace(",", ".")
        # Remove currency symbols
        normalized = re.sub(r"[₪$€£]", "", normalized).strip()
        try:
            return Decimal(normalized)
        except InvalidOperation:
            return Decimal("0")
