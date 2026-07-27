"""Cross-cutting HTTP middleware: request correlation and rate limiting."""

import time
from collections import defaultdict, deque

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.errors import REQUEST_ID_HEADER, new_request_id
from app.utils.logger import logger


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assign every request an id, echo it back, and bind it for logging.

    An inbound X-Request-ID is honoured so a caller can correlate across its
    own logs, but it is length-capped: the value is echoed in a response
    header, and an unbounded client-controlled header is worth not trusting.
    """

    MAX_INBOUND_LENGTH = 64

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        inbound = request.headers.get(REQUEST_ID_HEADER, "").strip()
        if inbound and len(inbound) <= self.MAX_INBOUND_LENGTH and inbound.isalnum():
            request_id = inbound
            from app.utils.errors import _request_id

            _request_id.set(request_id)
        else:
            request_id = new_request_id()

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-capacity sliding window per client, applied to the document routes.

    State lives in process memory, so the limit is per instance rather than
    global. That is sufficient for the current single-instance deployment and
    is documented as a known limitation in the README; moving to more than one
    instance means moving this to Redis.
    """

    def __init__(
        self,
        app,  # type: ignore[no-untyped-def]
        max_requests: int,
        window_seconds: int,
        path_prefix: str = "/api/v1/documents",
        trust_proxy_headers: bool = True,
    ) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.path_prefix = path_prefix
        self.trust_proxy_headers = trust_proxy_headers
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _client_key(self, request: Request) -> str:
        if self.trust_proxy_headers:
            # Render terminates TLS and appends the real client IP as the first
            # entry. Only meaningful behind a proxy that overwrites the header;
            # direct exposure makes this spoofable, hence the setting.
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if self.max_requests <= 0 or not request.url.path.startswith(self.path_prefix):
            return await call_next(request)

        key = self._client_key(request)
        now = time.monotonic()
        cutoff = now - self.window_seconds

        hits = self._hits[key]
        while hits and hits[0] < cutoff:
            hits.popleft()

        if len(hits) >= self.max_requests:
            retry_after = max(1, int(hits[0] + self.window_seconds - now))
            logger.warning("Rate limit exceeded for %s on %s", key, request.url.path)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too many requests"},
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)

        # Keys for idle clients would otherwise accumulate for the process
        # lifetime; drop the empty deques opportunistically.
        if len(self._hits) > 10_000:
            for stale_key in [k for k, v in self._hits.items() if not v]:
                del self._hits[stale_key]

        return await call_next(request)
