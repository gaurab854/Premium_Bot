"""
Inline keyboard builders — reusable InlineKeyboardMarkup factories.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_feedback_keyboard() -> InlineKeyboardMarkup:
    """Build a 1–5 star rating inline keyboard."""
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        builder.add(
            InlineKeyboardButton(
                text="⭐" * i,
                callback_data=f"feedback:{i}",
            )
        )
    builder.adjust(3, 2)  # 3 buttons on first row, 2 on second
    return builder.as_markup()


def get_confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    """Build a generic Yes/No confirmation keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Yes", callback_data=f"confirm:{action}:yes"),
        InlineKeyboardButton(text="❌ No", callback_data=f"confirm:{action}:no"),
    )
    return builder.as_markup()
