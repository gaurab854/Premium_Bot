"""
SQLAlchemy declarative base with common columns.

Every model inherits from `Base` and automatically receives:
  - id          (BigInt primary key)
  - created_at  (server-default UTC timestamp)
  - updated_at  (auto-updated UTC timestamp)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Abstract declarative base with audit columns."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        cols = ", ".join(
            f"{k}={getattr(self, k)!r}"
            for k in self.__table__.columns.keys()  # noqa: SIM118
        )
        return f"<{self.__class__.__name__}({cols})>"
