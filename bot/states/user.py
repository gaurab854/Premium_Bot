"""
User FSM states — multi-step conversation flows.
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
    States for the manual deposit flow.

    Flow:
        1. User sends /deposit → bot asks for amount
        2. User enters amount  → bot asks for TXID
        3. User enters TXID    → bot saves pending deposit
                                  & alerts admins
    """

    waiting_for_amount = State()
    waiting_for_txid = State()
