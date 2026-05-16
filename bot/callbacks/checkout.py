"""
Checkout and PromoCode CallbackData factories.
"""

from __future__ import annotations

from enum import Enum

from aiogram.filters.callback_data import CallbackData


class CheckoutAction(str, Enum):
    """User's checkout choice."""
    PAY = "pay"


class CheckoutCallback(CallbackData, prefix="checkout"):
    """
    Inline button payload for the checkout screen.
    Serialises to: checkout:<action>:<product_id>
    """
    action: CheckoutAction
    product_id: int


class GmailInviteCallback(CallbackData, prefix="gmail_invite"):
    """
    Admin button to confirm a Gmail invite was sent.
    """
    order_id: int
    user_id: int
