"""
User model — core Telegram user identity and admin flag.

One-to-one:  User ↔ Wallet
One-to-many: User → Orders, Transactions, Deposits, AdminLogs
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base

if TYPE_CHECKING:
    from database.models.wallet import Wallet
    from database.models.order import Order
    from database.models.transaction import Transaction
    from database.models.deposit import Deposit
    from database.models.admin_log import AdminLog


class User(Base):
    """Represents a Telegram user who has interacted with the bot."""

    __tablename__ = "users"

    # ── Telegram identifiers ──────────────────────────────────
    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
        comment="Unique Telegram user ID",
    )
    username: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="Telegram @username (may be null)",
    )
    first_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="",
        comment="Telegram first name",
    )
    last_name: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        comment="Telegram last name",
    )
    language_code: Mapped[Optional[str]] = mapped_column(
        String(10),
        nullable=True,
        comment="IETF language tag from Telegram",
    )

    # ── Bot-level flags ───────────────────────────────────────
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        comment="Whether the user has admin privileges",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        comment="Soft-delete / ban flag",
    )

    # ── Relationships ─────────────────────────────────────────
    wallet: Mapped[Optional["Wallet"]] = relationship(
        "Wallet",
        back_populates="user",
        uselist=False,
        lazy="joined",
        cascade="all, delete-orphan",
    )
    orders: Mapped[list["Order"]] = relationship(
        "Order",
        back_populates="user",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        back_populates="user",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    deposits: Mapped[list["Deposit"]] = relationship(
        "Deposit",
        back_populates="user",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    admin_logs: Mapped[list["AdminLog"]] = relationship(
        "AdminLog",
        back_populates="admin",
        foreign_keys="AdminLog.admin_id",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
