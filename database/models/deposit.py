"""
Deposit model — tracks crypto / fiat top-up requests.

Each deposit has a unique txid, an amount, and a status that
progresses through pending → confirmed / rejected.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base

if TYPE_CHECKING:
    from database.models.user import User


class DepositStatus(str, enum.Enum):
    """Lifecycle states of a deposit."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Deposit(Base):
    """Records an individual deposit / top-up attempt."""

    __tablename__ = "deposits"

    # ── Foreign key ───────────────────────────────────────────
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User who initiated the deposit",
    )

    # ── Deposit data ──────────────────────────────────────────
    txid: Mapped[str] = mapped_column(
        String(256),
        unique=True,
        nullable=False,
        index=True,
        comment="Blockchain / payment-gateway transaction ID",
    )
    amount: Mapped[float] = mapped_column(
        Float(precision=2),
        nullable=False,
        comment="Deposit amount (USD equivalent)",
    )
    status: Mapped[DepositStatus] = mapped_column(
        Enum(DepositStatus, name="deposit_status", create_constraint=True),
        default=DepositStatus.PENDING,
        server_default="pending",
        nullable=False,
        index=True,
        comment="Current deposit status",
    )
    note: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Admin note or rejection reason",
    )

    # ── Relationships ─────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="deposits",
        lazy="joined",
    )
