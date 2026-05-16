"""
Product model — catalogue of items available for purchase.

One-to-many: Product → Inventory (stock of hidden codes).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base

if TYPE_CHECKING:
    from database.models.inventory import Inventory
    from database.models.order_item import OrderItem


class Product(Base):
    """A purchasable product listed in the bot's catalogue."""

    __tablename__ = "products"

    # ── Product info ──────────────────────────────────────────
    name: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        index=True,
        comment="Product display name",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Detailed product description",
    )
    price: Mapped[float] = mapped_column(
        Float(precision=2),
        nullable=False,
        comment="Unit price (USD)",
    )
    category: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        index=True,
        comment="Product category for filtering",
    )
    is_available: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Whether the product is listed for sale",
    )
    allow_promo: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
        comment="Whether the product accepts promo codes",
    )

    # ── Relationships ─────────────────────────────────────────
    inventory_items: Mapped[list["Inventory"]] = relationship(
        "Inventory",
        back_populates="product",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    order_items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="product",
        lazy="selectin",
    )
