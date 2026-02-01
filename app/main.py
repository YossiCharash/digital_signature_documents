"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import settings
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


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Send documents via email (as attachment) or SMS (link to S3 download).",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(router, prefix="/api/v1")


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
