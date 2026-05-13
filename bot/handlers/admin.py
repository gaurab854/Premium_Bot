"""
Admin-only handlers — /stats, /ban, /unban, and deposit approval callbacks.

All **message** handlers in this router are guarded by ``AdminFilter``.
The callback-query handlers are guarded by an explicit admin check inside
the handler, because CallbackQuery events don't carry AdminFilter context.
"""

from __future__ import annotations

import structlog
from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.callbacks.deposit import DepositAction, DepositCallback
from bot.filters.admin import AdminFilter
from config import settings
from database import async_session_factory
from database.repositories.deposit import DepositRepository
from database.repositories.user import UserRepository
from database.repositories.wallet import WalletRepository

logger = structlog.get_logger()

router = Router(name="admin")
router.message.filter(AdminFilter())  # Guard all message handlers


# ═════════════════════════════════════════════════════════════
#  MESSAGE HANDLERS  (/stats, /ban, /unban)
# ═════════════════════════════════════════════════════════════

@router.message(Command("stats"))
async def cmd_stats(
    message: Message,
    user_repo: UserRepository,
    deposit_repo: DepositRepository,
) -> None:
    """Show bot statistics — users and pending deposits."""
    active = await user_repo.count_active()
    total = await user_repo.count_all()
    pending = await deposit_repo.count_pending()
    await message.answer(
        f"📊 <b>Bot Statistics</b>\n\n"
        f"<b>Total users:</b>    {total}\n"
        f"<b>Active users:</b>   {active}\n"
        f"<b>Pending deposits:</b> {pending}",
    )


@router.message(Command("ban"))
async def cmd_ban(message: Message, user_repo: UserRepository) -> None:
    """
    Ban a user by Telegram ID.
    Usage: /ban <telegram_id>
    """
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("⚠️ Usage: <code>/ban &lt;telegram_id&gt;</code>")
        return

    target_id = int(args[1].strip())
    await user_repo.set_active(target_id, active=False)
    await message.answer(f"🚫 User <code>{target_id}</code> has been banned.")


@router.message(Command("unban"))
async def cmd_unban(message: Message, user_repo: UserRepository) -> None:
    """
    Unban a user by Telegram ID.
    Usage: /unban <telegram_id>
    """
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("⚠️ Usage: <code>/unban &lt;telegram_id&gt;</code>")
        return

    target_id = int(args[1].strip())
    await user_repo.set_active(target_id, active=True)
    await message.answer(f"✅ User <code>{target_id}</code> has been unbanned.")


# ═════════════════════════════════════════════════════════════
#  CALLBACK HANDLERS  (Deposit Approve / Reject)
# ═════════════════════════════════════════════════════════════
#
#  WHY WE USE session.begin() HERE:
#
#  The approve flow performs TWO writes that MUST be atomic:
#    1. UPDATE deposits SET status = 'confirmed'  WHERE status = 'pending'
#    2. UPDATE wallets  SET balance = balance + amount
#
#  If we credit the wallet but crash before marking the deposit
#  as confirmed, a restart would process the same deposit again
#  → the user gets credited TWICE (double-crediting bug).
#
#  By wrapping both writes in a single ``async with session.begin()``
#  block, PostgreSQL guarantees:
#    • EITHER both writes commit together,
#    • OR neither write persists (on error the TX rolls back).
#
#  The additional ``WHERE status = 'pending'`` guard on the UPDATE
#  ensures that even if an admin clicks "Approve" twice, only the
#  FIRST click succeeds (rowcount == 1); the second click sees
#  status is already 'confirmed' and returns rowcount == 0.
#
# ═════════════════════════════════════════════════════════════

@router.callback_query(DepositCallback.filter())
async def handle_deposit_decision(
    callback: CallbackQuery,
    callback_data: DepositCallback,
    bot: Bot,
) -> None:
    """
    Admin presses Approve or Reject on a deposit notification.

    Uses a **fresh session with explicit session.begin()** to ensure
    the status transition + balance credit are atomic and idempotent.
    """
    # ── Admin guard (callback queries bypass router-level filter) ─
    if callback.from_user.id not in settings.bot.admin_ids:
        await callback.answer("🚫 You are not an admin.", show_alert=True)
        return

    deposit_id = callback_data.deposit_id
    action = callback_data.action

    # ── Open a FRESH session for atomic approve/reject ────────
    #    We intentionally do NOT use the middleware-injected session
    #    here because we need explicit begin()/commit() control
    #    to guarantee atomicity of the multi-step operation.
    async with async_session_factory() as session:
        async with session.begin():
            # Instantiate repos on this dedicated session
            deposit_repo = DepositRepository(session)
            wallet_repo = WalletRepository(session)
            user_repo = UserRepository(session)

            # ── Fetch the deposit ─────────────────────────────
            deposit = await deposit_repo.get_by_id(deposit_id)

            if deposit is None:
                await callback.answer(
                    "⚠️ Deposit not found.",
                    show_alert=True,
                )
                return

            # ──────────────────────────────────────────────────
            #  APPROVE FLOW
            # ──────────────────────────────────────────────────
            if action == DepositAction.APPROVE:
                # Attempt to transition status: pending → confirmed
                # Returns False if already processed (idempotency guard)
                was_updated = await deposit_repo.approve(
                    deposit_id,
                    note=f"Approved by admin {callback.from_user.id}",
                )

                if not was_updated:
                    await callback.answer(
                        "⚠️ This deposit has already been processed.",
                        show_alert=True,
                    )
                    return

                # Credit the user's wallet atomically
                wallet = await wallet_repo.get_or_create(
                    user_id=deposit.user_id,
                )
                await wallet_repo.add_balance(
                    wallet_id=wallet.id,
                    amount=deposit.amount,
                )

                logger.info(
                    "Deposit approved",
                    deposit_id=deposit_id,
                    user_id=deposit.user_id,
                    amount=deposit.amount,
                    admin_id=callback.from_user.id,
                )

                # ── session.begin() auto-commits here ─────────
                # Both the status change AND the balance credit
                # are committed in ONE atomic transaction.

            # ──────────────────────────────────────────────────
            #  REJECT FLOW
            # ──────────────────────────────────────────────────
            elif action == DepositAction.REJECT:
                was_updated = await deposit_repo.reject(
                    deposit_id,
                    note=f"Rejected by admin {callback.from_user.id}",
                )

                if not was_updated:
                    await callback.answer(
                        "⚠️ This deposit has already been processed.",
                        show_alert=True,
                    )
                    return

                logger.info(
                    "Deposit rejected",
                    deposit_id=deposit_id,
                    user_id=deposit.user_id,
                    admin_id=callback.from_user.id,
                )

    # ── Update the admin's message (remove buttons) ───────────
    # (This runs AFTER the session has committed successfully)

    if action == DepositAction.APPROVE:
        status_text = "✅ <b>APPROVED</b>"
        status_emoji = "✅"
        new_balance_line = ""

        # Fetch updated balance to show in the message
        async with async_session_factory() as session:
            w_repo = WalletRepository(session)
            wallet = await w_repo.get_by_user_id(deposit.user_id)
            if wallet:
                new_balance_line = (
                    f"\n<b>New Balance:</b>  ${wallet.balance:.2f}"
                )

        await callback.message.edit_text(
            f"{callback.message.text}\n\n"
            f"─────────────────────\n"
            f"{status_text} by "
            f"<code>{callback.from_user.id}</code>"
            f"{new_balance_line}",
        )
    else:
        status_text = "❌ <b>REJECTED</b>"
        await callback.message.edit_text(
            f"{callback.message.text}\n\n"
            f"─────────────────────\n"
            f"{status_text} by "
            f"<code>{callback.from_user.id}</code>",
        )

    await callback.answer(f"Deposit {action.value}d.", show_alert=False)

    # ── Notify the user ───────────────────────────────────────
    try:
        # Look up the user's telegram_id
        async with async_session_factory() as session:
            u_repo = UserRepository(session)
            db_user = await u_repo.get_by_id(deposit.user_id)

        if db_user:
            if action == DepositAction.APPROVE:
                await bot.send_message(
                    db_user.telegram_id,
                    f"✅ <b>Deposit Approved!</b>\n\n"
                    f"<b>Amount:</b> ${deposit.amount:.2f}\n"
                    f"<b>TXID:</b>   <code>{deposit.txid}</code>\n\n"
                    f"Your balance has been credited. "
                    f"Check /balance to see your updated balance.",
                )
            else:
                await bot.send_message(
                    db_user.telegram_id,
                    f"❌ <b>Deposit Rejected</b>\n\n"
                    f"<b>Amount:</b> ${deposit.amount:.2f}\n"
                    f"<b>TXID:</b>   <code>{deposit.txid}</code>\n\n"
                    f"Your deposit was not approved. "
                    f"Please contact support if you believe this is an error.",
                )
    except Exception as e:
        logger.warning(
            "Failed to notify user about deposit decision",
            user_id=deposit.user_id,
            error=str(e),
        )
