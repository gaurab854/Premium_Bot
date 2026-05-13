"""
Reply keyboard builders — persistent reply keyboards.
"""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def get_main_menu() -> ReplyKeyboardMarkup:
    """Build the main menu reply keyboard."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📋 My Profile"),
        KeyboardButton(text="💬 Feedback"),
    )
    builder.row(
        KeyboardButton(text="❓ Help"),
    )
    return builder.as_markup(
        resize_keyboard=True,
        input_field_placeholder="Choose an option …",
    )
