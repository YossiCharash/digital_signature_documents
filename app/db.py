"""Async SQLAlchemy database setup."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

engine = None
async_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    pass


def init_db(database_url: str) -> None:
    """Initialize the async engine and session factory."""
    global engine, async_session_factory
    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def create_tables() -> None:
    """Create all tables that are registered on Base.metadata."""
    if engine is None:
        return
    from app.models.short_link import ShortLink  # noqa: F401 – registers model with metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an AsyncSession."""
    if async_session_factory is None:
        raise RuntimeError("Database not initialised – DATABASE_URL is not configured.")
    async with async_session_factory() as session:
        yield session
