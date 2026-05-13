"""
Purchase CallbackData factories.

Encodes product browsing and purchase confirmation callbacks:
  • ``product:view:<product_id>``      — view product details
  • ``product:buy:<product_id>``       — confirm purchase
  • ``product:cancel:<product_id>``    — cancel purchase
  • ``product:category:<category>``    — browse by category
"""

from __future__ import annotations

from enum import Enum

from aiogram.filters.callback_data import CallbackData


class ProductAction(str, Enum):
    """Actions available on a product."""

    VIEW = "view"
    BUY = "buy"
    CANCEL = "cancel"


class ProductCallback(CallbackData, prefix="product"):
    """
    Typed callback payload for product interactions.

    Serialises to: ``product:<action>:<product_id>``
    """

    action: ProductAction
    product_id: int


class CategoryCallback(CallbackData, prefix="category"):
    """
    Typed callback for category browsing.

    Serialises to: ``category:<name>``
    """

    name: str
