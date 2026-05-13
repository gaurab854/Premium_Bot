"""
Wallet model — one-to-one with User.

Stores the user's floating-point balance and links to transaction history.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base

if TYPE_CHECKING:
    from database.models.user import User
    from database.models.transaction import Transaction


class Wallet(Base):
    """Each user has exactly one wallet holding their balance."""

    __tablename__ = "wallets"

    # ── Foreign key (one-to-one with users) ───────────────────
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
        comment="Owning user (one-to-one)",
    )

    # ── Balance ───────────────────────────────────────────────
    balance: Mapped[float] = mapped_column(
        Float(precision=2),
        default=0.0,
        server_default="0.0",
        nullable=False,
        comment="Current wallet balance (USD)",
    )

    # ── Relationships ─────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="wallet",
        lazy="joined",
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        back_populates="wallet",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
