"""
Order model — groups purchased items into a single checkout.

One-to-many: Order → OrderItems.
Many-to-one: Order → User.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Enum, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base

if TYPE_CHECKING:
    from database.models.user import User
    from database.models.order_item import OrderItem


class OrderStatus(str, enum.Enum):
    """Lifecycle states of an order."""

    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class Order(Base):
    """A purchase order placed by a user."""

    __tablename__ = "orders"

    # ── Foreign key ───────────────────────────────────────────
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User who placed the order",
    )

    # ── Order data ────────────────────────────────────────────
    total_amount: Mapped[float] = mapped_column(
        Float(precision=2),
        nullable=False,
        default=0.0,
        comment="Total order cost (USD)",
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status", create_constraint=True),
        default=OrderStatus.PENDING,
        server_default="pending",
        nullable=False,
        index=True,
        comment="Current order status",
    )

    # ── Relationships ─────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="orders",
        lazy="joined",
    )
    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="order",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
