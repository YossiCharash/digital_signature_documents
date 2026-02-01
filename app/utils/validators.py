"""Validation utilities for document delivery."""

import re


def validate_email(email: str) -> bool:
    """Validate email address format."""
    if not email or not isinstance(email, str):
        return False
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def validate_phone_number(phone: str) -> bool:
    """Validate phone number (9–12 digits, optional +/spaces)."""
    if not phone or not isinstance(phone, str):
        return False
    digits = re.sub(r"\D", "", phone)
    return 9 <= len(digits) <= 12
