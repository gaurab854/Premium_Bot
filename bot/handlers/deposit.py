"""
Deposit handler — user-facing deposit flow with payment method selection.

Payment methods supported:
    1. Bybit UID   : 547991243
    2. BEP-20      : 0xe40b02a757bb2714d4671812dc66254dd1e8ebd9
    3. Plasma      : 0xe40b02a757bb2714d4671812dc66254dd1e8ebd9

Flow:
    /deposit  →  choose payment method  →  show address / UID
              →  user sends amount      →  user sends transaction hash
              →  save pending deposit   →  alert admins with Approve/Reject
"""

from __future__ import annotations

import aiohttp
import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks.deposit import DepositAction, DepositCallback
from bot.states.user import DepositForm
from config import settings
from database import async_session_factory
from database.repositories.deposit import DepositRepository
from database.repositories.user import UserRepository
from database.repositories.wallet import WalletRepository

logger = structlog.get_logger()

router = Router(name="deposit")

# ── Payment methods ────────────────────────────────────────────────────────
PAYMENT_METHODS = {
    "bybit": {
        "label": "🟡 Bybit UID (Manual)",
        "display": "Bybit UID",
        "value": "547991243",
        "instructions": (
            "📌 <b>How to pay via Bybit:</b>\n\n"
            "1️⃣ Open Bybit → <b>Assets</b> → <b>P2P / Transfer</b>\n"
            "2️⃣ Transfer to UID: <code>547991243</code>\n"
            "3️⃣ Copy your <b>Transaction ID / Hash</b> after sending\n"
            "4️⃣ Come back here and send it to us"
        ),
    },
    "trc20": {
        "label": "🟢 TRC-20 (Automatic)",
        "display": "TRC-20 (Tron)",
        "value": settings.trc20_wallet_address,
        "instructions": (
            "📌 <b>How to pay via TRC-20:</b>\n\n"
            "1️⃣ Open your wallet or exchange\n"
            "2️⃣ Send USDT on the <b>TRC-20 (Tron)</b> network to:\n"
            f"<code>{settings.trc20_wallet_address}</code>\n"
            "3️⃣ Copy your <b>Transaction Hash (TxID)</b>\n"
            "4️⃣ Come back here and send it to us\n\n"
            "⚡ <i>TRC-20 deposits are verified automatically!</i>"
        ),
    },
    "bep20": {
        "label": "🔵 BEP-20 (Manual)",
        "display": "BEP-20",
        "value": "0xe40b02a757bb2714d4671812dc66254dd1e8ebd9",
        "instructions": (
            "📌 <b>How to pay via BEP-20:</b>\n\n"
            "1️⃣ Open your wallet (Trust Wallet / MetaMask / Binance)\n"
            "2️⃣ Send USDT or BNB on the <b>BEP-20 (BSC)</b> network to:\n"
            "<code>0xe40b02a757bb2714d4671812dc66254dd1e8ebd9</code>\n"
            "3️⃣ Copy your <b>Transaction Hash (TxID)</b> from BscScan\n"
            "4️⃣ Come back here and send it to us"
        ),
    },
}

async def verify_tron_txid(txid: str, expected_amount: float, your_wallet: str) -> bool:
    """
    Queries the Tronscan API to verify if a given TXID is a valid, 
    confirmed transfer of USDT to the specified wallet.
    """
    url = f"https://apilist.tronscanapi.com/api/transaction-info?hash={txid}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
        
        # 1. Check if the transaction is confirmed on-chain
        if data.get("confirmed") != True:
            return False

        # 2. Iterate through TRC20 transfers to find matching recipient and amount
        for transfer in data.get("trc20TransferInfo", []):
            to_address = transfer.get("to_address", "").lower()
            amount_str = transfer.get("amount_str", "0")
            
            # USDT has 6 decimals, so divide by 1_000_000
            actual_amount = float(amount_str) / 1_000_000
            
            if to_address == your_wallet.lower() and actual_amount >= expected_amount:
                return True
                
        return False
    except Exception as e:
        logger.error("Error verifying Tron TXID", txid=txid, error=str(e))
        return False


def _payment_method_keyboard():
    builder = InlineKeyboardBuilder()
    for key, method in PAYMENT_METHODS.items():
        builder.row(
            InlineKeyboardButton(
                text=method["label"],
                callback_data=f"pay_method:{key}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_deposit"),
    )
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1:  /deposit → choose payment method
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("deposit"))
async def cmd_deposit(message: Message, state: FSMContext) -> None:
    """Start the deposit flow — prompt user to select a payment method."""
    await state.clear()
    await message.answer(
        "💳 <b>Add Funds — Choose Payment Method</b>\n\n"
        "Select how you would like to pay:",
        reply_markup=_payment_method_keyboard(),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2:  User picks a payment method → show address + ask for amount
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay_method:"))
async def on_payment_method_selected(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    method_key = callback.data.split(":")[1]
    method = PAYMENT_METHODS.get(method_key)

    if not method:
        await callback.answer("Invalid method.", show_alert=True)
        return

    await state.update_data(payment_method=method_key)
    await state.set_state(DepositForm.waiting_for_amount)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_deposit"),
    )

    await callback.message.edit_text(
        f"{method['instructions']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>How much are you depositing? (in USD)</b>\n\n"
        f"<i>Example: 25.00</i>\n\n"
        f"Send /cancel at any time to abort.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_deposit")
async def on_cancel_deposit_cb(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Deposit cancelled.")
    await callback.answer()


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3:  User sends amount → ask for transaction hash
# ─────────────────────────────────────────────────────────────────────────────

@router.message(DepositForm.waiting_for_amount, F.text)
async def process_amount(message: Message, state: FSMContext) -> None:
    """Validate the deposit amount and ask for the transaction hash."""
    raw = message.text.strip()

    try:
        amount = float(raw)
    except ValueError:
        await message.answer(
            "⚠️ Invalid number. Please enter a valid amount (e.g. <code>25.00</code>)."
        )
        return

    if amount <= 0:
        await message.answer("⚠️ Amount must be greater than zero.")
        return

    if amount > 100_000:
        await message.answer("⚠️ Maximum deposit is $100,000.")
        return

    fsm_data = await state.get_data()
    method_key = fsm_data.get("payment_method", "bep20")
    method = PAYMENT_METHODS[method_key]

    await state.update_data(deposit_amount=amount)
    await state.set_state(DepositForm.waiting_for_txid)

    await message.answer(
        f"✅ <b>Amount: <u>${amount:.2f}</u></b>\n"
        f"Method: <b>{method['display']}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Now send your <b>Transaction Hash / TXID</b> "
        f"from the payment you made to:\n"
        f"<code>{method['value']}</code>\n\n"
        f"<i>Paste the full hash — it will be verified by an admin.</i>\n\n"
        f"Send /cancel to abort."
    )


# ─────────────────────────────────────────────────────────────────────────────
#  CANCEL — abort the deposit flow from any state
#  IMPORTANT: These must be registered BEFORE process_txid so that the
#  Command("cancel") filter wins over the generic F.text filter.
# ─────────────────────────────────────────────────────────────────────────────

@router.message(DepositForm.waiting_for_amount, Command("cancel"))
@router.message(DepositForm.waiting_for_txid, Command("cancel"))
async def cmd_cancel_deposit(message: Message, state: FSMContext) -> None:
    """Cancel the deposit flow at any stage."""
    await state.clear()
    await message.answer("❌ Deposit cancelled.")


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4:  User sends TXID → save deposit → alert admins
# ─────────────────────────────────────────────────────────────────────────────

@router.message(DepositForm.waiting_for_txid, F.text)
async def process_txid(
    message: Message,
    state: FSMContext,
    bot: Bot,
    user_repo: UserRepository,
    deposit_repo: DepositRepository,
) -> None:
    """
    Validate the TXID, save a PENDING deposit, and notify admins
    with Approve / Reject inline buttons.
    """
    txid = message.text.strip()

    # Guard: never treat a command as a TXID.
    # This is a safety net — the cancel handler above should catch /cancel first.
    if txid.startswith("/"):
        await message.answer(
            "⚠️ That doesn't look like a transaction hash.\n"
            "Send /cancel to abort the deposit."
        )
        return

    if len(txid) < 6:
        await message.answer(
            "⚠️ That TXID looks too short. "
            "Please send the full transaction hash / reference."
        )
        return

    if len(txid) > 256:
        await message.answer("⚠️ TXID is too long (max 256 characters).")
        return

    existing = await deposit_repo.get_by_txid(txid)
    if existing is not None:
        await message.answer(
            "⚠️ This TXID has already been submitted. "
            "Please contact support if you believe this is an error."
        )
        await state.clear()
        return

    db_user = await user_repo.get_by_telegram_id(message.from_user.id)
    if not db_user:
        await message.answer("⚠️ You are not registered. Please /start first.")
        await state.clear()
        return

    fsm_data = await state.get_data()
    amount = fsm_data["deposit_amount"]
    method_key = fsm_data.get("payment_method", "bep20")
    method = PAYMENT_METHODS[method_key]

    # ── Auto-Verification Logic for TRC-20 ──
    if method_key == "trc20":
        wait_msg = await message.answer("⏳ Verifying your transaction on the blockchain...")
        
        is_valid = await verify_tron_txid(txid, amount, method["value"])
        
        if is_valid:
            # ✅ Transaction is valid! Auto-approve it.
            deposit = await deposit_repo.create(
                user_id=db_user.id,
                txid=txid,
                amount=amount,
            )
            
            # Immediately mark as approved and credit wallet using atomic transaction
            async with async_session_factory() as session:
                async with session.begin():
                    # We need the repository instance tied to this session
                    session_deposit_repo = DepositRepository(session)
                    wallet_repo = WalletRepository(session)
                    
                    await session_deposit_repo.approve(
                        deposit.id, note="Auto-approved via TronScan API"
                    )
                    
                    wallet = await wallet_repo.get_or_create(user_id=db_user.id)
                    await wallet_repo.add_balance(wallet.id, amount)

            logger.info("Deposit auto-approved", txid=txid, user_id=db_user.id, amount=amount)
            await wait_msg.delete()
            await message.answer(
                f"✅ <b>Deposit Verified Automatically!</b>\n\n"
                f"<b>Amount:</b> ${amount:.2f} has been credited to your wallet.\n"
                f"Use /shop to start purchasing.",
                parse_mode="HTML",
            )
            await state.clear()
            return
            
        else:
            await wait_msg.delete()
            await message.answer(
                "⚠️ <b>Automatic Verification Failed.</b>\n"
                "We couldn't verify this transaction automatically. "
                "It has been submitted for manual review by an admin."
            )
            # Fall through to manual review flow

    # ── Manual Review Flow (for other methods or failed TRC20 verification) ──
    deposit = await deposit_repo.create(
        user_id=db_user.id,
        txid=txid,
        amount=amount,
    )

    logger.info(
        "Deposit created (pending)",
        deposit_id=deposit.id,
        user_id=db_user.id,
        method=method_key,
        txid=txid,
        amount=amount,
    )

    await state.clear()

    await message.answer(
        f"✅ <b>Deposit Submitted!</b>\n\n"
        f"<b>Amount:</b>   ${amount:.2f}\n"
        f"<b>Method:</b>   {method['display']}\n"
        f"<b>TXID:</b>     <code>{txid}</code>\n"
        f"<b>Status:</b>   ⏳ Pending review\n\n"
        f"An admin will verify your transaction shortly.\n"
        f"You'll be notified once it's approved or rejected."
    )

    # ── Build Approve / Reject keyboard for admins ─────────────
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

    admin_text = (
        f"🔔 <b>New Deposit Request</b>\n\n"
        f"<b>Deposit ID:</b>  #{deposit.id}\n"
        f"<b>User:</b>        {db_user.first_name} "
        f"(@{db_user.username or '—'})\n"
        f"<b>Telegram ID:</b> <code>{db_user.telegram_id}</code>\n"
        f"<b>Amount:</b>      ${amount:.2f}\n"
        f"<b>Method:</b>      {method['display']}\n"
        f"<b>Address:</b>     <code>{method['value']}</code>\n"
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



