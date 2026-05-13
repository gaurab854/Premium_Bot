"""
OrderItem model — individual line items within an order.

Links an Order to a Product and (optionally) to the specific
Inventory item that was fulfilled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base

if TYPE_CHECKING:
    from database.models.order import Order
    from database.models.product import Product
    from database.models.inventory import Inventory


class OrderItem(Base):
    """A single line item inside an order."""

    __tablename__ = "order_items"

    # ── Foreign keys ──────────────────────────────────────────
    order_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Parent order",
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Purchased product",
    )
    inventory_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("inventory.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Specific inventory item fulfilled (null until delivered)",
    )

    # ── Line-item data ────────────────────────────────────────
    quantity: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        comment="Number of units ordered",
    )
    price_at_purchase: Mapped[float] = mapped_column(
        Float(precision=2),
        nullable=False,
        comment="Snapshot of unit price at time of purchase",
    )

    # ── Relationships ─────────────────────────────────────────
    order: Mapped["Order"] = relationship(
        "Order",
        back_populates="items",
        lazy="joined",
    )
    product: Mapped["Product"] = relationship(
        "Product",
        back_populates="order_items",
        lazy="joined",
    )
    inventory: Mapped[Optional["Inventory"]] = relationship(
        "Inventory",
        lazy="joined",
    )
