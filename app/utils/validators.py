"""Validation utilities for invoice data."""

import re
from typing import Optional

from app.utils.logger import logger


class ValidationError(Exception):
    """Raised when validation fails."""

    pass


def validate_israeli_business_id(business_id: str) -> bool:
    """Validate Israeli business ID (9 digits with checksum)."""
    if not business_id or not isinstance(business_id, str):
        return False

    # Remove non-digit characters
    digits = re.sub(r"\D", "", business_id)
    if len(digits) != 9:
        return False

    # Luhn-like algorithm for Israeli business ID
    total = 0
    for i, digit in enumerate(digits[:8]):
        num = int(digit)
        if i % 2 == 0:
            total += num
        else:
            doubled = num * 2
            total += doubled if doubled < 10 else doubled - 9

    check_digit = (10 - (total % 10)) % 10
    return check_digit == int(digits[8])


def validate_email(email: str) -> bool:
    """Validate email address format."""
    if not email or not isinstance(email, str):
        return False

    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def validate_phone_number(phone: str) -> bool:
    """Validate Israeli phone number format."""
    if not phone or not isinstance(phone, str):
        return False

    # Remove non-digit characters
    digits = re.sub(r"\D", "", phone)
    # Israeli phone: 9-10 digits, may start with 0 or country code
    if len(digits) == 9:
        return digits.startswith("0")
    if len(digits) == 10:
        return digits.startswith("0") or digits.startswith("972")
    if len(digits) == 12:
        return digits.startswith("972")
    return False


def validate_invoice_number(invoice_number: str) -> bool:
    """Validate invoice number format."""
    if not invoice_number or not isinstance(invoice_number, str):
        return False
    # Invoice number should be non-empty and reasonable length
    return 1 <= len(invoice_number.strip()) <= 50


def sanitize_string(value: str, max_length: Optional[int] = None) -> str:
    """Sanitize string input."""
    if not isinstance(value, str):
        value = str(value)
    sanitized = value.strip()
    if max_length:
        sanitized = sanitized[:max_length]
    return sanitized


def validate_vat_rate(vat_rate: float) -> bool:
    """Validate VAT rate (0-100)."""
    return 0.0 <= vat_rate <= 100.0


def validate_amount(amount: float) -> bool:
    """Validate monetary amount (non-negative)."""
    return amount >= 0.0
