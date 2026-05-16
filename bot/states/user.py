"""
User FSM states — all multi-step conversation flows for the bot.
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class FeedbackForm(StatesGroup):
    """States for the feedback collection flow."""
    waiting_for_rating = State()
    waiting_for_comment = State()


class RegistrationForm(StatesGroup):
    """States for extended user registration (if needed)."""
    waiting_for_email = State()
    waiting_for_phone = State()
    confirmation = State()


class DepositForm(StatesGroup):
    """
    Manual deposit flow.
    1. /deposit → choose payment method
    2. Method chosen → enter amount
    3. Amount entered → enter TXID/hash
    4. TXID entered → saved pending, admins alerted
    """
    waiting_for_amount = State()
    waiting_for_txid = State()


class AddProductForm(StatesGroup):
    """
    Admin add-product FSM.
    Steps: name → price → category (LINK/GMAIL) → description → inventory codes
    """
    waiting_for_name = State()
    waiting_for_price = State()
    waiting_for_category = State()
    waiting_for_description = State()
    waiting_for_codes = State()


class EditProductForm(StatesGroup):
    """Admin edit-product flow."""
    waiting_for_product_id = State()
    waiting_for_field = State()
    waiting_for_new_value = State()


class CheckoutForm(StatesGroup):
    """
    Checkout flow — entered when user taps [Buy] on a product.
    If product is GMAIL, asks user to reply with Gmail address.
    """
    waiting_for_gmail = State()


class AddStockForm(StatesGroup):
    """
    Admin add-stock FSM.
    Steps: /addstock <id> → waiting_for_codes (admin sends codes one per line)
    """
    waiting_for_codes = State()
