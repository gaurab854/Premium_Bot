"""
Admin filter — restricts handlers to bot administrators.

Admin IDs are defined in settings.bot.admin_ids.
"""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message

from config import settings


class AdminFilter(BaseFilter):
    """Pass only if the sender's Telegram ID is in the admin list."""

    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in settings.bot.admin_ids
