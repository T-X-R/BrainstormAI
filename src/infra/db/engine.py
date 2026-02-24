"""SQLAlchemy async engine and session management."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.settings import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    """Create engine, session factory, and all tables."""
    global _engine, _session_factory

    settings = get_settings()
    _engine = create_async_engine(
        settings.database.url,
        echo=settings.app.debug,
    )
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Create all tables
    from src.infra.db.models import Base

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose the engine."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory (must be called after init_db)."""
    assert _session_factory is not None, "Database not initialized. Call init_db() first."
    return _session_factory
