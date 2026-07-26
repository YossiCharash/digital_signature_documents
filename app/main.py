"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.api.shortlink_routes import shortlink_router
from app.config import settings
from app.db import create_tables, init_db
from app.middleware import RateLimitMiddleware, RequestIDMiddleware
from app.services.scheduler import SchedulerService
from app.services.storage_service import StorageService
from app.utils.logger import logger

DOCUMENT_HINT = (
    "Do NOT send JSON. Use Content-Type: multipart/form-data. "
    "Required: 'file' (PDF), 'email'. Optional: 'subject', 'body'. "
    "Example: curl -X POST ... -F 'file=@doc.pdf' -F 'email=you@example.com'"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    settings.ensure_directories()

    # Authentication is staged so that already-distributed clients keep working
    # (see app/api/dependencies.py). While it is off the signing and delivery
    # endpoints are open, which must never be silent.
    configured_keys = settings.api_key_map
    if configured_keys:
        logger.info(
            "API key authentication enabled for %d caller(s): %s",
            len(configured_keys),
            ", ".join(sorted(configured_keys.values())),
        )
    else:
        logger.warning(
            "API_KEYS is not set: /api/v1/documents/* accepts unauthenticated "
            "requests. Anyone who can reach this service can sign documents "
            "with the configured key and send mail from the configured sender. "
            "Set API_KEYS once your clients send the X-API-Key header."
        )

    # Initialise URL-shortener database (optional)
    if settings.database_url:
        init_db(settings.database_url)
        await create_tables()
        logger.info("URL shortener database initialised")
    else:
        logger.info("DATABASE_URL not set – URL shortener disabled")

    # Initialize and start scheduler for cleanup jobs
    storage_service = StorageService()
    scheduler_service = SchedulerService(storage_service)
    scheduler_service.start()
    logger.info("Scheduler service started")

    logger.info("Application startup complete")
    yield
    # Shutdown
    scheduler_service.shutdown()
    logger.info("Application shutdown")


# The interactive docs describe every parameter of the signing and delivery
# endpoints, so they are served only when DEBUG is on. In production they would
# hand any scanner a ready-made request builder.
_docs_enabled = settings.debug

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Send documents via email (as attachment) or SMS (link to S3 download).",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Rate limiting runs before the route handlers but after request ids are
# assigned, so a 429 is still traceable. Starlette applies middleware in
# reverse registration order, hence RequestIDMiddleware is added last.
app.add_middleware(
    RateLimitMiddleware,
    max_requests=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_window_seconds,
    trust_proxy_headers=settings.trust_proxy_headers,
)
app.add_middleware(RequestIDMiddleware)

# CORS. This is a server-to-server API: the default is no cross-origin browser
# access at all, and credentials are never allowed because authentication uses
# the X-API-Key header rather than cookies.
_cors_origins = settings.cors_origin_list
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
    )

# Include routers
app.include_router(router, prefix="/api/v1")
app.include_router(shortlink_router)  # GET /r/{slug} – no prefix so URLs stay short


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return 422 with validation details and a hint for document endpoints."""
    detail = exc.errors()
    payload: dict[str, object] = {"detail": detail}
    if "/documents/" in request.url.path:
        hint = DOCUMENT_HINT
        ct = request.headers.get("content-type", "")
        if "multipart/form-data" not in ct:
            payload["content_type_received"] = ct or "(none)"
            hint += " Your request had Content-Type: " + (ct or "missing") + "."
        payload["hint"] = hint
    logger.warning("Validation error on %s: %s", request.url.path, detail)
    return JSONResponse(status_code=422, content=payload)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "operational",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}
