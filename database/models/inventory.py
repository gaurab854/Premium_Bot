"""
Inventory model — individual stock items (hidden codes) for a product.

Each row holds a secret `data` value (e.g. license key, gift card code)
that is revealed to the buyer upon purchase.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base

if TYPE_CHECKING:
    from database.models.product import Product


class Inventory(Base):
    """A single stock unit containing hidden code data."""

    __tablename__ = "inventory"

    # ── Foreign key ───────────────────────────────────────────
    product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Product this inventory item belongs to",
    )

    # ── Hidden code data ──────────────────────────────────────
    data: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Secret code / key / credentials (revealed on purchase)",
    )

    # ── Sale status ───────────────────────────────────────────
    is_sold: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
        index=True,
        comment="Whether this item has been sold",
    )
    sold_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the item was sold",
    )
    buyer_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User who purchased this item",
    )

    # ── Relationships ─────────────────────────────────────────
    product: Mapped["Product"] = relationship(
        "Product",
        back_populates="inventory_items",
        lazy="joined",
    )
