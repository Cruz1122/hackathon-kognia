from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_DATABASE_URL = "postgresql+asyncpg://kognia:kognia@localhost:15432/kognia"

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_database_url() -> str:
    """Return the configured async PostgreSQL URL, with a local-dev default."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL).strip()


def get_engine() -> AsyncEngine:
    """Create the process-wide async engine on first use."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_database_url(),
            pool_pre_ping=True,
            connect_args={"timeout": 5},
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield one short-lived async session for future request dependencies."""
    async with get_session_factory()() as session:
        yield session


async def check_database() -> None:
    """Verify that PostgreSQL accepts a lightweight connection."""
    async with get_engine().connect() as connection:
        await connection.execute(text("SELECT 1"))


async def dispose_engine() -> None:
    """Release the process-wide engine during application shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
