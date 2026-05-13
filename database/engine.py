"""
Async SQLAlchemy engine and session factory.

Provides:
  - `async_engine`          — the single AsyncEngine instance
  - `async_session_factory` — the sessionmaker bound to that engine
  - `get_session()`         — async context manager yielding a session
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings

# ──────────────────────────────────────────────────────────────
# Engine
# ──────────────────────────────────────────────────────────────
async_engine = create_async_engine(
    url=settings.postgres.async_url,
    echo=settings.debug,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
)

# ──────────────────────────────────────────────────────────────
# Session Factory
# ──────────────────────────────────────────────────────────────
async_session_factory = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ──────────────────────────────────────────────────────────────
# Convenience context manager
# ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an async session that auto-commits on success
    and rolls back on exception.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
