"""
Database package.

Exposes the async engine, session factory, Base declarative model,
and all ORM models for convenient imports:

    from database import Base, async_engine, get_session
    from database.models import User, Wallet, Order
"""

from database.base import Base
from database.engine import async_engine, async_session_factory, get_session

# Ensure all models are imported so metadata is populated
import database.models  # noqa: F401

__all__ = [
    "Base",
    "async_engine",
    "async_session_factory",
    "get_session",
]
