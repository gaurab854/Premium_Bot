"""
ChannelMemberFilter — checks whether a user is a member of the required channel.

Usage:
    router.message.filter(ChannelMemberFilter())

If BOT_CHANNEL_ID is not configured the filter always passes (no gate).
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.filters import BaseFilter
from aiogram.types import Message

from config import settings


class ChannelMemberFilter(BaseFilter):
    """
    Passes when the user is a member / admin / creator of the required channel.
    Fails (returns False) when the user has not joined yet.
    """

    async def __call__(self, message: Message, bot: Bot) -> bool:
        # If no channel is configured, gate is open for everyone
        if not settings.bot.channel_id:
            return True

        try:
            member = await bot.get_chat_member(
                chat_id=settings.bot.channel_id,
                user_id=message.from_user.id,
            )
            return member.status not in ("left", "kicked")
        except Exception:
            # If we can't check (e.g. bot not in channel), let user through
            return True
