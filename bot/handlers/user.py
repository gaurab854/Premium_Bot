"""
User-specific handlers — commands available to regular users.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.keyboards.inline import get_feedback_keyboard

router = Router(name="user")


@router.message(Command("feedback"))
async def cmd_feedback(message: Message) -> None:
    """Prompt user to leave feedback via inline keyboard."""
    await message.answer(
        "💬 <b>We value your feedback!</b>\n\n"
        "How would you rate your experience?",
        reply_markup=get_feedback_keyboard(),
    )
