"""Async SQLAlchemy database setup."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

engine = None
async_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    pass


def _normalize_async_url(database_url: str) -> str:
    """Coerce a plain Postgres URL to the async (asyncpg) driver.

    Managed hosts (Render, Heroku, …) hand out ``postgres://`` or
    ``postgresql://`` connection strings, but ``create_async_engine`` needs an
    async driver – otherwise SQLAlchemy tries the sync psycopg2 dialect and
    fails. URLs that already name a driver (``postgresql+asyncpg://``,
    ``sqlite+aiosqlite://``, …) are left untouched.
    """
    if database_url.startswith("postgres://"):
        return "postgresql+asyncpg://" + database_url[len("postgres://") :]
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def init_db(database_url: str) -> None:
    """Initialize the async engine and session factory."""
    global engine, async_session_factory
    engine = create_async_engine(
        _normalize_async_url(database_url), echo=False, pool_pre_ping=True
    )
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def create_tables() -> None:
    """Create all tables that are registered on Base.metadata."""
    if engine is None:
        return
    from app.models.delivery_log import DeliveryLog  # noqa: F401 – registers model with metadata
    from app.models.email_queue import EmailQueue  # noqa: F401 – registers model with metadata
    from app.models.short_link import ShortLink  # noqa: F401 – registers model with metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an AsyncSession."""
    if async_session_factory is None:
        raise RuntimeError("Database not initialised – DATABASE_URL is not configured.")
    async with async_session_factory() as session:
        yield session
