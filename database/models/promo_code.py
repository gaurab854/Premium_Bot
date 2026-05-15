"""
PromoCode model — admin-managed discount / access codes.

Each code can be single-use or multi-use, product-specific or global.
When a user submits a promo code, an order is created with status
PENDING_APPROVAL until an admin approves or rejects it.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base


class PromoCode(Base):
    """A discount or access code redeemable during checkout."""

    __tablename__ = "promo_codes"

    # ── Code ──────────────────────────────────────────────────
    code: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
        comment="The promo code string (case-insensitive match)",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Admin note on what this code grants",
    )

    # ── Scope ─────────────────────────────────────────────────
    # If product_id is None the code applies to any product.
    product_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Restrict code to a specific product (null = any product)",
    )

    # ── Usage limits ──────────────────────────────────────────
    max_uses: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        comment="Maximum times this code can be used (0 = unlimited)",
    )
    used_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
        comment="How many times this code has been successfully used",
    )

    # ── Status ────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
        comment="Whether the code can currently be submitted",
    )

    # ── Admin approval mode ───────────────────────────────────
    # When True, every use of this code must be manually approved.
    # When False, the code is auto-approved on submission.
    requires_approval: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
        comment="Require admin approval before fulfilling the order",
    )
