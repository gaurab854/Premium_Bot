"""
ChannelMemberMiddleware — enforces channel membership for ALL user messages.

If BOT_CHANNEL_ID is set in settings, every message and callback from a
non-admin user is checked against the channel.  Non-members see a join
prompt and the update is blocked from reaching any handler.

Bot must be an admin of the channel for get_chat_member to work.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import Bot, BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
    TelegramObject,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings


def _join_keyboard() -> Any:
    builder = InlineKeyboardBuilder()
    channel_link = (
        f"https://t.me/{settings.bot.channel_username.lstrip('@')}"
        if settings.bot.channel_username
        else "https://t.me/"
    )
    builder.row(
        InlineKeyboardButton(text="📢 Join Channel", url=channel_link),
    )
    builder.row(
        InlineKeyboardButton(
            text="✅ I've Joined — Check Again",
            callback_data="check_membership",
        ),
    )
    return builder.as_markup()


async def _is_member(bot: Bot, user_id: int) -> bool:
    """True if the user is a member/admin/creator of the configured channel."""
    if not settings.bot.channel_id:
        return True
    try:
        member = await bot.get_chat_member(
            chat_id=settings.bot.channel_id,
            user_id=user_id,
        )
        return member.status not in ("left", "kicked")
    except Exception:
        return True  # fail open if bot can't read channel


class ChannelMemberMiddleware(BaseMiddleware):
    """
    Outer middleware that gates all message/callback updates behind
    a channel membership check.

    Exemptions:
      - Admin users (always pass through)
      - The 'check_membership' callback (so the join-check button works)
      - Updates with no from_user (channel posts, etc.)
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Resolve the user from the event
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            # Always let the check_membership callback through so the
            # "I've Joined" button can actually verify membership
            if event.data == "check_membership":
                return await handler(event, data)
            user = event.from_user

        if user is None:
            return await handler(event, data)

        # Admins bypass the gate
        if user.id in settings.bot.admin_ids:
            return await handler(event, data)

        # No channel configured → open gate
        if not settings.bot.channel_id:
            return await handler(event, data)

        bot: Bot = data["bot"]
        if await _is_member(bot, user.id):
            return await handler(event, data)

        # ── User is not a member → block & prompt ─────────────
        channel_display = settings.bot.channel_username or "our channel"

        if isinstance(event, Message):
            await event.answer(
                f"🔒 <b>Join Required</b>\n\n"
                f"You must join {channel_display} before using this bot.\n\n"
                f"After joining, tap the button below to continue.",
                reply_markup=_join_keyboard(),
            )
        elif isinstance(event, CallbackQuery):
            await event.answer(
                "🔒 You must join our channel first!",
                show_alert=True,
            )
            try:
                await event.message.answer(
                    f"🔒 <b>Join Required</b>\n\n"
                    f"You must join {channel_display} before using this bot.\n\n"
                    f"After joining, tap the button below to continue.",
                    reply_markup=_join_keyboard(),
                )
            except Exception:
                pass

        # Do NOT call the handler — update is blocked
        return None
