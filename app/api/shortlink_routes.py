"""Redirect route: GET /r/{slug}  →  303 redirect to original URL."""

from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.url_shortener_service import get_short_link

shortlink_router = APIRouter(tags=["shortlinks"])


async def _db_dependency() -> AsyncGenerator[AsyncSession, None]:
    """Yields an AsyncSession or raises 503 when the DB is not configured."""
    from app.db import async_session_factory

    if async_session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="URL shortener is not configured (DATABASE_URL missing).",
        )
    async with async_session_factory() as session:
        yield session


@shortlink_router.get(
    "/r/{slug}",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    summary="Redirect a short link to its original URL",
)
async def redirect_short_link(slug: str) -> RedirectResponse:
    """Resolve *slug* and redirect to the stored long URL.

    Returns **302** on success, **404** when the slug is unknown,
    and **503** when the database is not configured.
    """
    async for db in _db_dependency():
        link = await get_short_link(db, slug)
        if link is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Short link '{slug}' not found.",
            )
        return RedirectResponse(url=link.long_url, status_code=status.HTTP_302_FOUND)
