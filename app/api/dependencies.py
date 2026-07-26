"""Shared FastAPI dependencies: service construction.

The services were previously instantiated at module import time in routes.py,
which built an S3 client and read the signing key as a side effect of importing
the router. That made the routes untestable without real configuration. Here
they are built on first use and cached, so importing the app is free and tests
can substitute fakes through ``app.dependency_overrides``.
"""

from functools import lru_cache

from app.services.email_service import EmailService
from app.services.signing_service import SigningService
from app.services.sms_service import SMSService
from app.services.storage_service import StorageService


@lru_cache(maxsize=1)
def get_storage_service() -> StorageService:
    return StorageService()


@lru_cache(maxsize=1)
def get_email_service() -> EmailService:
    return EmailService()


@lru_cache(maxsize=1)
def get_sms_service() -> SMSService:
    return SMSService()


@lru_cache(maxsize=1)
def get_signing_service() -> SigningService:
    """Build the signing service on first use.

    Kept lazy so the app still starts when PRIVATE_KEY_PEM / PRIVATE_KEY_PATH
    are unset - only the signing endpoints then fail, rather than the process.
    """
    return SigningService()
