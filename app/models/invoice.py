"""Invoice data models for Israeli Tax Authority compliance."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from app.utils.validators import (
    validate_amount,
    validate_invoice_number,
    validate_israeli_business_id,
    validate_vat_rate,
)


class Address(BaseModel):
    """Address model."""

    street: str = Field(..., min_length=1, max_length=200)
    city: str = Field(..., min_length=1, max_length=100)
    postal_code: Optional[str] = Field(default=None, max_length=20)
    country: str = Field(default="Israel", max_length=100)


class Supplier(BaseModel):
    """Supplier information model."""

    name: str = Field(..., min_length=1, max_length=200)
    business_id: str = Field(..., description="Israeli business ID (9 digits)")
    address: Address
    email: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=20)

    @field_validator("business_id")
    @classmethod
    def validate_business_id(cls, v: str) -> str:
        """Validate Israeli business ID."""
        from app.utils.validators import validate_israeli_business_id

        if not validate_israeli_business_id(v):
            raise ValueError("Invalid Israeli business ID format")
        return v


class Customer(BaseModel):
    """Customer information model."""

    name: str = Field(..., min_length=1, max_length=200)
    business_id: Optional[str] = Field(default=None, description="Israeli business ID (9 digits)")
    address: Optional[Address] = None
    email: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=20)

    @field_validator("business_id")
    @classmethod
    def validate_business_id(cls, v: Optional[str]) -> Optional[str]:
        """Validate Israeli business ID if provided."""
        if v is None:
            return v
        from app.utils.validators import validate_israeli_business_id

        if not validate_israeli_business_id(v):
            raise ValueError("Invalid Israeli business ID format")
        return v


class LineItem(BaseModel):
    """Invoice line item model."""

    description: str = Field(..., min_length=1, max_length=500)
    quantity: Decimal = Field(..., gt=0, decimal_places=3)
    unit_price: Decimal = Field(..., ge=0, decimal_places=2)
    vat_rate: Decimal = Field(..., ge=0, le=100, decimal_places=2)
    line_total: Decimal = Field(..., ge=0, decimal_places=2)
    line_total_vat: Decimal = Field(..., ge=0, decimal_places=2)
    line_total_with_vat: Decimal = Field(..., ge=0, decimal_places=2)

    @field_validator("line_total", "line_total_vat", "line_total_with_vat")
    @classmethod
    def validate_totals(cls, v: Decimal, info) -> Decimal:
        """Validate line totals are properly calculated."""
        if not validate_amount(float(v)):
            raise ValueError("Line totals must be non-negative")
        return v


class InvoiceItem(BaseModel):
    """Invoice item for digital invoice format."""

    item_id: str = Field(default_factory=lambda: str(uuid4()))
    description: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: Decimal
    total_excluding_vat: Decimal
    vat_amount: Decimal
    total_including_vat: Decimal


class DigitalInvoice(BaseModel):
    """Digital invoice model compliant with Israeli Tax Authority requirements."""

    invoice_id: UUID = Field(default_factory=uuid4)
    invoice_number: str = Field(..., min_length=1, max_length=50)
    issue_date: date
    supplier: Supplier
    customer: Customer
    items: List[InvoiceItem] = Field(..., min_items=1)
    subtotal_excluding_vat: Decimal = Field(..., ge=0, decimal_places=2)
    total_vat: Decimal = Field(..., ge=0, decimal_places=2)
    total_including_vat: Decimal = Field(..., ge=0, decimal_places=2)
    currency: str = Field(default="ILS", max_length=3)
    allocation_number: Optional[str] = Field(
        default=None, description="Future: Israeli Tax Authority allocation number"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict = Field(default_factory=dict)

    @field_validator("invoice_number")
    @classmethod
    def validate_invoice_number(cls, v: str) -> str:
        """Validate invoice number."""
        if not validate_invoice_number(v):
            raise ValueError("Invalid invoice number format")
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        """Validate currency code."""
        if len(v) != 3 or not v.isalpha():
            raise ValueError("Currency must be a 3-letter ISO code")
        return v.upper()

    def to_json_dict(self) -> dict:
        """Convert to deterministic JSON dictionary for signing."""
        return {
            "invoice_id": str(self.invoice_id),
            "invoice_number": self.invoice_number,
            "issue_date": self.issue_date.isoformat(),
            "supplier": {
                "name": self.supplier.name,
                "business_id": self.supplier.business_id,
                "address": {
                    "street": self.supplier.address.street,
                    "city": self.supplier.address.city,
                    "postal_code": self.supplier.address.postal_code,
                    "country": self.supplier.address.country,
                },
                "email": self.supplier.email,
                "phone": self.supplier.phone,
            },
            "customer": {
                "name": self.customer.name,
                "business_id": self.customer.business_id,
                "address": (
                    {
                        "street": self.customer.address.street,
                        "city": self.customer.address.city,
                        "postal_code": self.customer.address.postal_code,
                        "country": self.customer.address.country,
                    }
                    if self.customer.address
                    else None
                ),
                "email": self.customer.email,
                "phone": self.customer.phone,
            },
            "items": [
                {
                    "item_id": item.item_id,
                    "description": item.description,
                    "quantity": str(item.quantity),
                    "unit_price": str(item.unit_price),
                    "vat_rate": str(item.vat_rate),
                    "total_excluding_vat": str(item.total_excluding_vat),
                    "vat_amount": str(item.vat_amount),
                    "total_including_vat": str(item.total_including_vat),
                }
                for item in self.items
            ],
            "subtotal_excluding_vat": str(self.subtotal_excluding_vat),
            "total_vat": str(self.total_vat),
            "total_including_vat": str(self.total_including_vat),
            "currency": self.currency,
            "allocation_number": self.allocation_number,
            "created_at": self.created_at.isoformat(),
        }
