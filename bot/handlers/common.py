"""
Common handlers — /start, /help, and fallback messages.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from database.repositories.user import UserRepository
from database.repositories.wallet import WalletRepository

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message, user_repo: UserRepository, wallet_repo: WalletRepository) -> None:
    """Handle /start — register/update user, create wallet, send welcome."""
    user = message.from_user
    db_user = await user_repo.get_or_create(
        telegram_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        language_code=user.language_code,
    )

    # Ensure the user has a wallet
    await wallet_repo.get_or_create(user_id=db_user.id)

    await message.answer(
        f"👋 <b>Welcome, {user.first_name}!</b>\n\n"
        "I'm your <b>Premium Bot</b>. Use /help to see available commands.",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handle /help — list available commands."""
    text = (
        "📖 <b>Available Commands</b>\n\n"
        "👤 <b>Account</b>\n"
        "/start    — Register & welcome\n"
        "/me       — Your profile info\n"
        "/balance  — Check wallet balance\n\n"
        "🏪 <b>Shopping</b>\n"
        "/shop     — Browse products\n"
        "/orders   — Your purchase history\n\n"
        "💰 <b>Wallet</b>\n"
        "/deposit  — Add funds to your wallet\n\n"
        "📋 <b>Other</b>\n"
        "/help     — Show this help message\n"
        "/feedback — Leave feedback\n"
        "/stats    — Bot statistics (admin only)\n"
    )
    await message.answer(text)


@router.message(Command("me"))
async def cmd_me(message: Message, user_repo: UserRepository, wallet_repo: WalletRepository) -> None:
    """Handle /me — show the user's stored profile and balance."""
    db_user = await user_repo.get_by_telegram_id(message.from_user.id)
    if not db_user:
        await message.answer("⚠️ You are not registered yet. Send /start first.")
        return

    balance = await wallet_repo.get_balance(user_id=db_user.id)

    await message.answer(
        f"👤 <b>Your Profile</b>\n\n"
        f"<b>Telegram ID:</b>  <code>{db_user.telegram_id}</code>\n"
        f"<b>Name:</b>         {db_user.first_name} {db_user.last_name or ''}\n"
        f"<b>Username:</b>     @{db_user.username or '—'}\n"
        f"<b>Language:</b>     {db_user.language_code or '—'}\n"
        f"<b>Admin:</b>        {'✅' if db_user.is_admin else '❌'}\n"
        f"<b>Balance:</b>      ${balance:.2f}\n"
        f"<b>Registered:</b>   {db_user.created_at:%Y-%m-%d %H:%M UTC}\n",
    )


@router.message(Command("balance"))
async def cmd_balance(message: Message, user_repo: UserRepository, wallet_repo: WalletRepository) -> None:
    """Handle /balance — show wallet balance."""
    db_user = await user_repo.get_by_telegram_id(message.from_user.id)
    if not db_user:
        await message.answer("⚠️ You are not registered yet. Send /start first.")
        return

    balance = await wallet_repo.get_balance(user_id=db_user.id)
    await message.answer(f"💰 <b>Your Balance:</b> <code>${balance:.2f}</code>")


@router.message()
async def fallback(message: Message) -> None:
    """Catch-all for unrecognised messages."""
    await message.answer(
        "🤔 I don't understand that. Try /help to see what I can do.",
    )
