"""Shared FastAPI dependencies: service construction and API-key authentication.

The services were previously instantiated at module import time in routes.py,
which built an S3 client and read the signing key as a side effect of importing
the router. That made the routes untestable without real configuration. Here
they are built on first use and cached, so importing the app is free and tests
can substitute fakes through ``app.dependency_overrides``.
"""

import hmac
from functools import lru_cache

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import settings
from app.services.email_service import EmailService
from app.services.signing_service import SigningService
from app.services.sms_service import SMSService
from app.services.storage_service import StorageService
from app.utils.logger import logger


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


# ---------------------------------------------------------------------------
# Authentication
#
# api_client.ps1 is already installed on many machines, so authentication is
# rolled out in stages rather than switched on at once:
#
#   1. Deploy this code with API_KEYS unset - every existing copy keeps working
#      and the app logs a warning on startup that auth is off.
#   2. Distribute the updated client, which sends the header regardless of
#      whether the server enforces it yet.
#   3. Set API_KEYS. Enforcement begins with no window of incompatibility.
#
# /deliveries is exempt from that staging: it returns every recipient email and
# phone number the service has handled, no distributed client calls it, and so
# it is never served without a key.
# ---------------------------------------------------------------------------

API_KEY_HEADER_NAME = "X-API-Key"

# auto_error=False so a missing header reaches the dependency, which decides
# between 401 and "not enforced" based on configuration.
_api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)

_UNAUTHORIZED = HTTPException(
    status.HTTP_401_UNAUTHORIZED,
    detail="Missing or invalid API key",
    headers={"WWW-Authenticate": API_KEY_HEADER_NAME},
)


def _match_key(candidate: str, keys: dict[str, str]) -> str | None:
    """Return the caller label for *candidate*, or None.

    Compares against every configured key with ``hmac.compare_digest`` and
    without an early exit, so neither the comparison itself nor the number of
    iterations leaks information about the correct key.

    Both sides are encoded to bytes first: compare_digest rejects str operands
    containing non-ASCII characters with a TypeError, which would otherwise
    turn a header like "X-API-Key: שלום" into a 500.
    """
    candidate_bytes = candidate.encode("utf-8", "surrogateescape")
    matched: str | None = None
    for key, label in keys.items():
        if hmac.compare_digest(key.encode("utf-8"), candidate_bytes):
            matched = label
    return matched


def require_api_key(api_key: str | None = Security(_api_key_header)) -> str:
    """Always require a valid key. Used for routes that expose stored PII.

    Raises 503 rather than allowing access when no keys are configured: an
    unconfigured deployment must fail closed here, not open.
    """
    keys = settings.api_key_map
    if not keys:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This endpoint requires API_KEYS to be configured",
        )

    if not api_key:
        raise _UNAUTHORIZED

    label = _match_key(api_key, keys)
    if label is None:
        logger.warning("Rejected request with an unrecognised API key")
        raise _UNAUTHORIZED
    return label


def require_api_key_when_configured(
    api_key: str | None = Security(_api_key_header),
) -> str | None:
    """Require a valid key only once API_KEYS is set.

    Returns the caller label, or None while enforcement is off. This is what
    keeps already-distributed clients working during the rollout above.
    """
    keys = settings.api_key_map
    if not keys:
        return None

    if not api_key:
        raise _UNAUTHORIZED

    label = _match_key(api_key, keys)
    if label is None:
        logger.warning("Rejected request with an unrecognised API key")
        raise _UNAUTHORIZED
    return label
