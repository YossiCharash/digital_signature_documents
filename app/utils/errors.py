"""Client-safe error responses.

Provider exceptions carry internal detail - bucket names, IAM ARNs, raw SMTP
and API responses - so they must never be interpolated into an HTTPException
detail. Instead the full exception is logged against a request id, and the
client receives a generic message plus that id, which is enough to correlate a
user's report with the log line.
"""

import uuid
from contextvars import ContextVar

from fastapi import HTTPException, status

from app.utils.logger import logger

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    """Generate a request id and bind it to the current context."""
    request_id = uuid.uuid4().hex[:12]
    _request_id.set(request_id)
    return request_id


def current_request_id() -> str | None:
    """Return the request id bound to the current context, if any."""
    return _request_id.get()


def internal_error(
    message: str,
    exc: Exception,
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
) -> HTTPException:
    """Log *exc* in full and return an HTTPException that leaks nothing.

    Returns the exception rather than raising it so callers keep an explicit
    ``raise ... from e`` and preserve the cause chain.
    """
    request_id = current_request_id()
    logger.error("[%s] %s: %s", request_id or "-", message, exc, exc_info=True)

    detail: dict[str, str] = {"error": message}
    if request_id:
        detail["request_id"] = request_id
    return HTTPException(status_code, detail=detail)
