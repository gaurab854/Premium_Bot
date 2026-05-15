"""
Checkout and PromoCode CallbackData factories.
"""

from __future__ import annotations

from enum import Enum

from aiogram.filters.callback_data import CallbackData


class CheckoutAction(str, Enum):
    """User's checkout choice."""
    PAY = "pay"
    PROMO = "promo"


class CheckoutCallback(CallbackData, prefix="checkout"):
    """
    Inline button payload for the checkout screen.
    Serialises to: checkout:<action>:<product_id>
    """
    action: CheckoutAction
    product_id: int


class PromoOrderAction(str, Enum):
    """Admin action on a promo order request."""
    APPROVE = "approve"
    REJECT = "reject"


class PromoOrderCallback(CallbackData, prefix="promo_order"):
    """
    Admin approve/reject buttons for promo-code orders.
    Serialises to: promo_order:<action>:<request_id>
    """
    action: PromoOrderAction
    request_id: int
