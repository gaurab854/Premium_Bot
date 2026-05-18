"""
Common handlers — /start, /help, /me, /balance, and fallback messages.

Channel-join gate:
  If BOT_CHANNEL_ID is set, /start checks membership before registering.
  Users who haven't joined see a prompt with a "Join Channel" button.
  All other commands also redirect non-members to join first.
"""

from __future__ import annotations

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks.purchase import ProductAction, ProductCallback
from config import settings
from database.repositories.inventory import InventoryRepository
from database.repositories.order import OrderRepository
from database.repositories.product import ProductRepository
from database.repositories.user import UserRepository
from database.repositories.wallet import WalletRepository

router = Router(name="common")


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _join_keyboard() -> InlineKeyboardMarkup:
    """Build a keyboard with a 'Join Channel' button."""
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
        InlineKeyboardButton(text="✅ I've Joined — Check Again", callback_data="check_membership"),
    )
    return builder.as_markup()


async def _is_member(bot: Bot, user_id: int) -> bool:
    """Return True if user_id is a member of the configured channel."""
    if not settings.bot.channel_id:
        return True
    try:
        member = await bot.get_chat_member(
            chat_id=settings.bot.channel_id,
            user_id=user_id,
        )
        return member.status not in ("left", "kicked")
    except Exception:
        return True  # fail open if bot can't access channel


# ─────────────────────────────────────────────────────────────────────────────
#  /start
# ─────────────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(
    message: Message,
    bot: Bot,
    user_repo: UserRepository,
    wallet_repo: WalletRepository,
) -> None:
    """Handle /start — check channel membership, register user, send welcome."""
    user = message.from_user

    # ── Channel gate ──────────────────────────────────────────
    if not await _is_member(bot, user.id):
        channel_display = settings.bot.channel_username or "our channel"
        await message.answer(
            f"👋 <b>Welcome to Premium Shop!</b>\n\n"
            f"🔒 To access this bot you must first join {channel_display}.\n\n"
            f"<i>After joining, tap the button below to verify.</i>",
            reply_markup=_join_keyboard(),
        )
        return

    # ── Register / update user ────────────────────────────────
    db_user = await user_repo.get_or_create(
        telegram_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        language_code=user.language_code,
    )
    await wallet_repo.get_or_create(user_id=db_user.id)

    # ── Welcome message ───────────────────────────────────────
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎁 Browse Products", callback_data="open_shop"),
        InlineKeyboardButton(text="💰 My Balance", callback_data="my_balance"),
    )
    builder.row(
        InlineKeyboardButton(text="💳 Deposit", callback_data="open_deposit"),
        InlineKeyboardButton(text="📋 My Orders", callback_data="my_orders"),
    )
    builder.row(
        InlineKeyboardButton(text="👤 My Profile", callback_data="my_profile"),
        InlineKeyboardButton(text="❓ Help", callback_data="open_help"),
    )

    await message.answer(
        f"👋 <b>Welcome, {user.first_name}!</b>\n\n"
        "🏪 <b>Premium Shop</b> — your one-stop digital goods store.\n\n"
        "Browse products, top up your balance, and get instant delivery "
        "of digital codes right here in Telegram.\n\n"
        "<i>Use the buttons below or /help to see all commands.</i>",
        reply_markup=builder.as_markup(),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Inline button — "I've Joined — Check Again"
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "check_membership")
async def check_membership_callback(
    callback: CallbackQuery,
    bot: Bot,
    user_repo: UserRepository,
    wallet_repo: WalletRepository,
) -> None:
    """Re-verify channel membership when the user taps the check button."""
    user = callback.from_user

    if not await _is_member(bot, user.id):
        await callback.answer(
            "❌ You haven't joined yet! Please join the channel first.",
            show_alert=True,
        )
        return

    # Joined — register and show welcome
    db_user = await user_repo.get_or_create(
        telegram_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        language_code=user.language_code,
    )
    await wallet_repo.get_or_create(user_id=db_user.id)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎁 Browse Products", callback_data="open_shop"),
        InlineKeyboardButton(text="💰 My Balance", callback_data="my_balance"),
    )
    builder.row(
        InlineKeyboardButton(text="💳 Deposit", callback_data="open_deposit"),
        InlineKeyboardButton(text="📋 My Orders", callback_data="my_orders"),
    )
    builder.row(
        InlineKeyboardButton(text="👤 My Profile", callback_data="my_profile"),
        InlineKeyboardButton(text="❓ Help", callback_data="open_help"),
    )

    await callback.message.edit_text(
        f"✅ <b>Verified!</b> Welcome, {user.first_name}!\n\n"
        "🏪 <b>Premium Shop</b> — your one-stop digital goods store.\n\n"
        "Browse products, top up your balance, and get instant delivery "
        "of digital codes right here in Telegram.\n\n"
        "<i>Use the buttons below or /help to see all commands.</i>",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────────────────────
#  Inline button callbacks — delegate directly to the real command functions
#  so there is zero duplicate logic anywhere.
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "open_deposit")
async def cb_open_deposit(callback: CallbackQuery, state: FSMContext) -> None:
    from bot.handlers.deposit import cmd_deposit
    await cmd_deposit(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "open_shop")
async def cb_open_shop(callback: CallbackQuery, product_repo: ProductRepository, inventory_repo: InventoryRepository) -> None:
    from bot.handlers.purchase import cmd_shop
    await cmd_shop(callback.message, product_repo, inventory_repo)
    await callback.answer()


@router.callback_query(F.data == "my_balance")
async def cb_my_balance(callback: CallbackQuery, user_repo: UserRepository, wallet_repo: WalletRepository) -> None:
    await cmd_balance(callback.message, user_repo, wallet_repo)
    await callback.answer()


@router.callback_query(F.data == "my_orders")
async def cb_my_orders(callback: CallbackQuery, user_repo: UserRepository, order_repo: OrderRepository) -> None:
    from bot.handlers.purchase import cmd_orders
    await cmd_orders(callback.message, user_repo, order_repo)
    await callback.answer()


@router.callback_query(F.data == "my_profile")
async def cb_my_profile(callback: CallbackQuery, user_repo: UserRepository, wallet_repo: WalletRepository) -> None:
    await cmd_me(callback.message, user_repo, wallet_repo)
    await callback.answer()


@router.callback_query(F.data == "open_help")
async def cb_open_help(callback: CallbackQuery) -> None:
    await cmd_help(callback.message)
    await callback.answer()





# ─────────────────────────────────────────────────────────────────────────────
#  Explicit text commands (also work without buttons)
# ─────────────────────────────────────────────────────────────────────────────

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
        "🛡️ <b>Support</b>\n"
        "/contact  — Warranty support & replacement\n\n"
        "📋 <b>Other</b>\n"
        "/help     — Show this help message\n"
    )
    await message.answer(text)


@router.message(Command("me"))
async def cmd_me(
    message: Message,
    user_repo: UserRepository,
    wallet_repo: WalletRepository,
) -> None:
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
async def cmd_balance(
    message: Message,
    user_repo: UserRepository,
    wallet_repo: WalletRepository,
) -> None:
    """Handle /balance — show wallet balance."""
    db_user = await user_repo.get_by_telegram_id(message.from_user.id)
    if not db_user:
        await message.answer("⚠️ You are not registered yet. Send /start first.")
        return

    balance = await wallet_repo.get_balance(user_id=db_user.id)
    await message.answer(f"💰 <b>Your Balance:</b> <code>${balance:.2f}</code>")


@router.message(Command("contact"))
async def cmd_contact(message: Message) -> None:
    """Show warranty support contact info."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="💬 Contact @Randompliw",
            url="https://t.me/Randompliw",
        )
    )
    await message.answer(
        "🛡️ <b>Warranty Support</b>\n\n"
        "For product replacements or warranty claims, contact our admin directly:\n\n"
        "👤 <b>@Randompliw</b>\n\n"
        "<i>Please include your Order ID and a description of the issue.</i>",
        reply_markup=builder.as_markup(),
    )


@router.message()
async def fallback(message: Message) -> None:
    """Catch-all for unrecognised messages."""
    await message.answer(
        "🤔 I don't understand that. Try /help to see what I can do.",
    )
