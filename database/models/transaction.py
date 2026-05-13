"""
Transaction model — ledger of all balance changes.

Every deposit, purchase, or refund creates a Transaction row
so the full financial history is auditable.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base

if TYPE_CHECKING:
    from database.models.user import User
    from database.models.wallet import Wallet


class TransactionType(str, enum.Enum):
    """Possible transaction types."""

    DEPOSIT = "deposit"
    PURCHASE = "purchase"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


class TransactionStatus(str, enum.Enum):
    """Possible transaction statuses."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"


class Transaction(Base):
    """Immutable ledger entry for every balance mutation."""

    __tablename__ = "transactions"

    # ── Foreign keys ──────────────────────────────────────────
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User who owns this transaction",
    )
    wallet_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Wallet affected by this transaction",
    )

    # ── Transaction data ──────────────────────────────────────
    amount: Mapped[float] = mapped_column(
        Float(precision=2),
        nullable=False,
        comment="Signed amount (+credit / -debit)",
    )
    type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, name="transaction_type", create_constraint=True),
        nullable=False,
        index=True,
        comment="Type of transaction",
    )
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, name="transaction_status", create_constraint=True),
        default=TransactionStatus.COMPLETED,
        server_default="completed",
        nullable=False,
        comment="Current status of the transaction",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Human-readable description / memo",
    )
    reference_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        index=True,
        comment="External reference (order ID, deposit txid, etc.)",
    )

    # ── Relationships ─────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="transactions",
        lazy="joined",
    )
    wallet: Mapped["Wallet"] = relationship(
        "Wallet",
        back_populates="transactions",
        lazy="joined",
    )
