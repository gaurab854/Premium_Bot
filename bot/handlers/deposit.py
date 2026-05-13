"""
Deposit handler — the user-facing manual deposit flow.

Flow:
    /deposit  →  ask amount  →  ask TXID  →  save pending deposit  →  alert admins

Uses Aiogram 3.x FSM (Finite State Machine) to collect input
across multiple messages without conflicting with other commands.
"""

from __future__ import annotations

import structlog
from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks.deposit import DepositAction, DepositCallback
from bot.states.user import DepositForm
from config import settings
from database.repositories.deposit import DepositRepository
from database.repositories.user import UserRepository
from database.repositories.wallet import WalletRepository

logger = structlog.get_logger()

router = Router(name="deposit")


# ─────────────────────────────────────────────────────────────
#  STEP 1:  /deposit  →  ask for amount
# ─────────────────────────────────────────────────────────────
@router.message(Command("deposit"))
async def cmd_deposit(message: Message, state: FSMContext) -> None:
    """Start the deposit flow — prompt the user for the deposit amount."""
    await state.set_state(DepositForm.waiting_for_amount)
    await message.answer(
        "💰 <b>Manual Deposit</b>\n\n"
        "Please enter the <b>deposit amount</b> in USD.\n\n"
        "<i>Example: 25.00</i>\n\n"
        "Send /cancel at any time to abort.",
    )


# ─────────────────────────────────────────────────────────────
#  STEP 2:  receive amount  →  ask for TXID
# ─────────────────────────────────────────────────────────────
@router.message(DepositForm.waiting_for_amount, F.text)
async def process_amount(message: Message, state: FSMContext) -> None:
    """Validate the deposit amount and ask for the TXID."""
    raw = message.text.strip()

    # ── Validate amount ───────────────────────────────────────
    try:
        amount = float(raw)
    except ValueError:
        await message.answer(
            "⚠️ Invalid number. Please enter a valid amount (e.g. <code>25.00</code>)."
        )
        return  # stay in the same state

    if amount <= 0:
        await message.answer("⚠️ Amount must be greater than zero.")
        return

    if amount > 100_000:
        await message.answer("⚠️ Maximum deposit is $100,000. Please enter a smaller amount.")
        return

    # ── Store amount in FSM data and advance to next state ────
    await state.update_data(deposit_amount=amount)
    await state.set_state(DepositForm.waiting_for_txid)

    await message.answer(
        f"✅ Amount: <b>${amount:.2f}</b>\n\n"
        "Now please send the <b>Transaction ID (TXID)</b> "
        "from your payment.\n\n"
        "<i>This is the hash or reference number from your "
        "wallet / payment provider.</i>\n\n"
        "Send /cancel to abort.",
    )


# ─────────────────────────────────────────────────────────────
#  STEP 3:  receive TXID  →  save deposit  →  alert admins
# ─────────────────────────────────────────────────────────────
@router.message(DepositForm.waiting_for_txid, F.text)
async def process_txid(
    message: Message,
    state: FSMContext,
    bot: Bot,
    user_repo: UserRepository,
    deposit_repo: DepositRepository,
) -> None:
    """
    Validate the TXID, save a PENDING deposit, and send an
    approval request to every admin with Approve/Reject buttons.
    """
    txid = message.text.strip()

    # ── Validate TXID format ──────────────────────────────────
    if len(txid) < 6:
        await message.answer(
            "⚠️ That TXID looks too short. "
            "Please send the full transaction hash / reference."
        )
        return

    if len(txid) > 256:
        await message.answer("⚠️ TXID is too long (max 256 characters).")
        return

    # ── Check for duplicate TXID ──────────────────────────────
    existing = await deposit_repo.get_by_txid(txid)
    if existing is not None:
        await message.answer(
            "⚠️ This TXID has already been submitted. "
            "Please check your deposit history or contact support."
        )
        await state.clear()
        return

    # ── Get the DB user ───────────────────────────────────────
    db_user = await user_repo.get_by_telegram_id(message.from_user.id)
    if not db_user:
        await message.answer("⚠️ You are not registered. Please /start first.")
        await state.clear()
        return

    # ── Retrieve amount from FSM data ─────────────────────────
    fsm_data = await state.get_data()
    amount = fsm_data["deposit_amount"]

    # ── Create the PENDING deposit ────────────────────────────
    deposit = await deposit_repo.create(
        user_id=db_user.id,
        txid=txid,
        amount=amount,
    )

    logger.info(
        "Deposit created",
        deposit_id=deposit.id,
        user_id=db_user.id,
        telegram_id=db_user.telegram_id,
        txid=txid,
        amount=amount,
    )

    # ── Clear the FSM — flow is complete for the user ─────────
    await state.clear()

    # ── Confirm to the user ───────────────────────────────────
    await message.answer(
        f"✅ <b>Deposit Submitted!</b>\n\n"
        f"<b>Amount:</b>  ${amount:.2f}\n"
        f"<b>TXID:</b>    <code>{txid}</code>\n"
        f"<b>Status:</b>  ⏳ Pending review\n\n"
        f"An admin will verify your transaction shortly.\n"
        f"You'll be notified once it's approved.",
    )

    # ── Build Approve / Reject inline keyboard ────────────────
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Approve",
            callback_data=DepositCallback(
                deposit_id=deposit.id,
                action=DepositAction.APPROVE,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Reject",
            callback_data=DepositCallback(
                deposit_id=deposit.id,
                action=DepositAction.REJECT,
            ).pack(),
        ),
    )

    # ── Alert every admin ─────────────────────────────────────
    admin_text = (
        f"🔔 <b>New Deposit Request</b>\n\n"
        f"<b>Deposit ID:</b>  #{deposit.id}\n"
        f"<b>User:</b>        {db_user.first_name} "
        f"(@{db_user.username or '—'})\n"
        f"<b>Telegram ID:</b> <code>{db_user.telegram_id}</code>\n"
        f"<b>Amount:</b>      ${amount:.2f}\n"
        f"<b>TXID:</b>        <code>{txid}</code>\n"
        f"<b>Status:</b>      ⏳ Pending"
    )

    for admin_id in settings.bot.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                admin_text,
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            logger.warning(
                "Failed to notify admin",
                admin_id=admin_id,
                error=str(e),
            )


# ─────────────────────────────────────────────────────────────
#  CANCEL — abort the deposit flow from any state
# ─────────────────────────────────────────────────────────────
@router.message(DepositForm.waiting_for_amount, Command("cancel"))
@router.message(DepositForm.waiting_for_txid, Command("cancel"))
async def cmd_cancel_deposit(message: Message, state: FSMContext) -> None:
    """Cancel the deposit flow at any stage."""
    await state.clear()
    await message.answer("❌ Deposit cancelled.")
