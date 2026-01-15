"""Invoice builder service to create digital invoices from parsed data."""

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional

from app.models.invoice import (
    Address,
    Customer,
    DigitalInvoice,
    InvoiceItem,
    LineItem,
    Supplier,
)
from app.utils.logger import logger


class InvoiceBuildError(Exception):
    """Raised when invoice building fails."""

    pass


class InvoiceBuilder:
    """Service for building digital invoices from parsed data."""

    def build(self, parsed_data: Dict) -> DigitalInvoice:
        """Build a digital invoice from parsed data."""
        try:
            logger.info(f"Building digital invoice for {parsed_data.get('invoice_number')}")

            # Extract and validate required fields
            invoice_number = parsed_data["invoice_number"]
            issue_date_str = parsed_data["issue_date"]
            issue_date = datetime.strptime(issue_date_str, "%Y-%m-%d").date()

            # Build supplier
            supplier_data = parsed_data["supplier"]
            supplier = self._build_supplier(supplier_data)

            # Build customer
            customer_data = parsed_data.get("customer")
            customer = self._build_customer(customer_data) if customer_data else self._build_default_customer()

            # Build line items
            line_items_data = parsed_data["line_items"]
            invoice_items = self._build_invoice_items(line_items_data)

            # Calculate totals
            subtotal_excluding_vat = sum(item.total_excluding_vat for item in invoice_items)
            total_vat = sum(item.vat_amount for item in invoice_items)
            total_including_vat = sum(item.total_including_vat for item in invoice_items)

            # Validate totals if provided
            totals = parsed_data.get("totals", {})
            if totals.get("total"):
                provided_total = Decimal(str(totals["total"]))
                # Allow small rounding differences
                if abs(total_including_vat - provided_total) > Decimal("0.01"):
                    logger.warning(
                        f"Total mismatch: calculated {total_including_vat}, provided {provided_total}"
                    )

            # Build digital invoice
            invoice = DigitalInvoice(
                invoice_number=invoice_number,
                issue_date=issue_date,
                supplier=supplier,
                customer=customer,
                items=invoice_items,
                subtotal_excluding_vat=subtotal_excluding_vat,
                total_vat=total_vat,
                total_including_vat=total_including_vat,
                currency="ILS",
                allocation_number=parsed_data.get("allocation_number"),
            )

            logger.info(f"Successfully built digital invoice {invoice_number}")
            return invoice

        except Exception as e:
            logger.error(f"Failed to build invoice: {e}")
            raise InvoiceBuildError(f"Invoice building failed: {e}") from e

    def _build_supplier(self, supplier_data: Dict) -> Supplier:
        """Build supplier model from data."""
        address_data = supplier_data.get("address")
        if address_data:
            address = Address(
                street=address_data.get("street", "Unknown"),
                city=address_data.get("city", "Unknown"),
                postal_code=address_data.get("postal_code"),
                country=address_data.get("country", "Israel"),
            )
        else:
            address = Address(street="Unknown", city="Unknown", country="Israel")

        return Supplier(
            name=supplier_data["name"],
            business_id=supplier_data.get("business_id", ""),
            address=address,
            email=supplier_data.get("email"),
            phone=supplier_data.get("phone"),
        )

    def _build_customer(self, customer_data: Dict) -> Customer:
        """Build customer model from data."""
        address_data = customer_data.get("address")
        address = None
        if address_data:
            address = Address(
                street=address_data.get("street", "Unknown"),
                city=address_data.get("city", "Unknown"),
                postal_code=address_data.get("postal_code"),
                country=address_data.get("country", "Israel"),
            )

        return Customer(
            name=customer_data.get("name", "Unknown Customer"),
            business_id=customer_data.get("business_id"),
            address=address,
            email=customer_data.get("email"),
            phone=customer_data.get("phone"),
        )

    def _build_default_customer(self) -> Customer:
        """Build default customer when customer data is missing."""
        return Customer(name="Unknown Customer", business_id=None, address=None)

    def _build_invoice_items(self, line_items_data: List[Dict]) -> List[InvoiceItem]:
        """Build invoice items from line items data."""
        invoice_items = []

        for line_item_data in line_items_data:
            # Calculate totals if not provided
            quantity = Decimal(str(line_item_data["quantity"]))
            unit_price = Decimal(str(line_item_data["unit_price"]))
            vat_rate = Decimal(str(line_item_data.get("vat_rate", "17.00")))

            total_excluding_vat = quantity * unit_price
            vat_amount = total_excluding_vat * (vat_rate / Decimal("100"))
            total_including_vat = total_excluding_vat + vat_amount

            # Use provided totals if available and more accurate
            if "line_total" in line_item_data:
                total_excluding_vat = Decimal(str(line_item_data["line_total"]))
            if "line_total_vat" in line_item_data:
                vat_amount = Decimal(str(line_item_data["line_total_vat"]))
            if "line_total_with_vat" in line_item_data:
                total_including_vat = Decimal(str(line_item_data["line_total_with_vat"]))
                # Recalculate VAT if total is provided
                vat_amount = total_including_vat - total_excluding_vat

            invoice_item = InvoiceItem(
                description=line_item_data["description"],
                quantity=quantity,
                unit_price=unit_price,
                vat_rate=vat_rate,
                total_excluding_vat=total_excluding_vat,
                vat_amount=vat_amount,
                total_including_vat=total_including_vat,
            )

            invoice_items.append(invoice_item)

        return invoice_items
