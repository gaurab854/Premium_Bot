"""
PromoOrderRequest model — tracks pending promo-code order approvals.

When a user submits a promo code at checkout, a row is inserted here
with status PENDING.  The admin approves or rejects it via inline
buttons, which triggers fulfilment (inventory lock + delivery).
"""

from __future__ import annotations

import enum
from typing import Optional

from sqlalchemy import BigInteger, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class PromoOrderStatus(str, enum.Enum):
    """Lifecycle of a promo-code checkout request."""

    PENDING = "PENDING"         # Submitted, awaiting admin action
    APPROVED = "APPROVED"       # Admin approved — product delivered
    REJECTED = "REJECTED"       # Admin rejected — user notified


class PromoOrderRequest(Base):
    """
    Represents a checkout attempt using a promo code.

    One row per submission.  The admin approves or rejects via
    callback buttons, which then triggers inventory fulfilment.
    """

    __tablename__ = "promo_order_requests"

    # ── Foreign keys ──────────────────────────────────────────
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User who submitted the promo code",
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Product the user is trying to get",
    )

    # ── Promo data ────────────────────────────────────────────
    promo_code: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="The code the user submitted",
    )

    # ── Status ────────────────────────────────────────────────
    status: Mapped[PromoOrderStatus] = mapped_column(
        Enum(PromoOrderStatus, name="promo_order_status", create_constraint=True),
        default=PromoOrderStatus.PENDING,
        server_default="PENDING",
        nullable=False,
        index=True,
        comment="Current approval status",
    )

    # ── Admin note ────────────────────────────────────────────
    admin_note: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Reason for rejection or admin note on approval",
    )

    # ── Telegram message reference (for editing admin message) ─
    admin_message_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Telegram message ID of the admin notification (for editing)",
    )
    admin_chat_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Chat ID where the admin notification was sent",
    )

    # ── Relationships ─────────────────────────────────────────
    user: Mapped["User"] = relationship(  # type: ignore[name-defined]
        "User",
        lazy="joined",
        foreign_keys=[user_id],
    )
    product: Mapped["Product"] = relationship(  # type: ignore[name-defined]
        "Product",
        lazy="joined",
        foreign_keys=[product_id],
    )
