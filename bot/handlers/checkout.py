"""
Checkout handler — the full buy flow with Pay / Promo Code branching.

Flow overview:
    User taps [View] on a product → sees details + [💳 Pay] [🎁 Promo Code]

    Branch A — Pay:
        Bot shows payment method selection (Bybit / BEP-20 / Plasma)
        User picks method → bot shows address + instructions
        → User goes to /deposit to fund wallet, then buys from /shop

    Branch B — Promo Code:
        Bot asks user to type their code
        User sends code → PromoOrderRequest saved as PENDING
        → Admin notified with [✅ Approve] [❌ Reject]
        → On approve: inventory locked + code delivered to user
        → On reject: user notified + checkout keyboard re-shown

Design notes:
    • The Pay path (wallet-funded instant purchase) is handled by purchase.py.
      This handler replaces the raw [Buy] button with a checkout gateway.
    • The promo path is fully manual approval — no wallet deduction.
    • All DB mutations in approve/reject use a fresh session with begin()
      for atomic inventory lock + order creation.
"""

from __future__ import annotations

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

from bot.callbacks.checkout import (
    CheckoutAction,
    CheckoutCallback,
    PromoOrderAction,
    PromoOrderCallback,
)
from bot.callbacks.purchase import ProductAction, ProductCallback
from bot.states.user import CheckoutForm
from config import settings
from database import async_session_factory
from database.models.promo_order_request import PromoOrderStatus
from database.repositories.inventory import InventoryRepository
from database.repositories.order import OrderRepository
from database.repositories.product import ProductRepository
from database.repositories.promo_code import PromoCodeRepository
from database.repositories.user import UserRepository
from database.repositories.wallet import WalletRepository
from database.models.promo_order_request import PromoOrderStatus

logger = structlog.get_logger()

router = Router(name="checkout")

# ── Payment info (shown in Pay flow) ──────────────────────────────────────────
PAYMENT_INFO = {
    "bybit": {
        "label": "🟡 Bybit UID",
        "value": "547991243",
        "note": "Transfer via Bybit P2P/Assets to this UID, then use /deposit to submit your TXID.",
    },
    "bep20": {
        "label": "🔵 BEP-20 (USDT/BNB)",
        "value": "0xe40b02a757bb2714d4671812dc66254dd1e8ebd9",
        "note": "Send USDT/BNB on BSC network to the address above, then use /deposit.",
    },
    "plasma": {
        "label": "🟣 Plasma",
        "value": "0xe40b02a757bb2714d4671812dc66254dd1e8ebd9",
        "note": "Send to the address above via Plasma network, then use /deposit.",
    },
}


def _checkout_keyboard(product_id: int):
    """The initial checkout choice keyboard shown to the user."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="💳 Pay",
            callback_data=CheckoutCallback(
                action=CheckoutAction.PAY,
                product_id=product_id,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🎁 Use Promo Code",
            callback_data=CheckoutCallback(
                action=CheckoutAction.PROMO,
                product_id=product_id,
            ).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Back to Shop",
            callback_data="back_to_shop",
        ),
    )
    return builder.as_markup()


def _pay_method_keyboard(product_id: int):
    """Payment method selection after user picks 'Pay'."""
    builder = InlineKeyboardBuilder()
    for key, info in PAYMENT_INFO.items():
        builder.row(
            InlineKeyboardButton(
                text=info["label"],
                callback_data=f"checkout_pay_method:{product_id}:{key}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Back",
            callback_data=CheckoutCallback(
                action=CheckoutAction.PAY,
                product_id=product_id,
            ).pack(),
        ),
    )
    return builder.as_markup()


# ═════════════════════════════════════════════════════════════════════════════
#  INTERCEPT the raw [Buy] callback and show checkout screen instead
# ═════════════════════════════════════════════════════════════════════════════

@router.callback_query(
    ProductCallback.filter(F.action == ProductAction.BUY),
)
async def on_checkout_entry(
    callback: CallbackQuery,
    callback_data: ProductCallback,
    product_repo: ProductRepository,
    inventory_repo: InventoryRepository,
    user_repo: UserRepository,
    wallet_repo: WalletRepository,
) -> None:
    """
    Intercept the [Buy] button — show the checkout gateway instead of
    immediately processing the purchase.
    """
    product = await product_repo.get_by_id(callback_data.product_id)
    if product is None or not product.is_available:
        await callback.answer("⚠️ Product no longer available.", show_alert=True)
        return

    stock = await inventory_repo.count_available(product.id)
    if stock == 0:
        await callback.answer("😔 Out of stock!", show_alert=True)
        return

    db_user = await user_repo.get_by_telegram_id(callback.from_user.id)
    balance = 0.0
    if db_user:
        balance = await wallet_repo.get_balance(user_id=db_user.id)

    await callback.message.edit_text(
        f"🛒 <b>Checkout</b>\n\n"
        f"<b>Product:</b>  {product.name}\n"
        f"<b>Price:</b>    ${product.price:.2f}\n"
        f"<b>Stock:</b>    {stock} unit(s)\n"
        f"<b>Balance:</b>  ${balance:.2f}\n\n"
        f"Choose how you'd like to proceed:",
        reply_markup=_checkout_keyboard(product.id),
    )
    await callback.answer()


# ═════════════════════════════════════════════════════════════════════════════
#  Branch A — PAY (wallet-funded instant purchase)
# ═════════════════════════════════════════════════════════════════════════════

@router.callback_query(CheckoutCallback.filter(F.action == CheckoutAction.PAY))
async def on_checkout_pay(
    callback: CallbackQuery,
    callback_data: CheckoutCallback,
    product_repo: ProductRepository,
    user_repo: UserRepository,
    wallet_repo: WalletRepository,
    inventory_repo: InventoryRepository,
    order_repo: OrderRepository,
) -> None:
    """
    User chose 'Pay'.

    If they have enough balance → process the purchase immediately (same
    atomic flow as before: lock inventory → deduct wallet → mark sold → deliver).

    If not enough balance → show payment options so they can deposit first.
    """
    product = await product_repo.get_by_id(callback_data.product_id)
    if product is None or not product.is_available:
        await callback.answer("⚠️ Product no longer available.", show_alert=True)
        return

    db_user = await user_repo.get_by_telegram_id(callback.from_user.id)
    if not db_user:
        await callback.answer("⚠️ Please /start first.", show_alert=True)
        return

    wallet = await wallet_repo.get_or_create(user_id=db_user.id)
    stock = await inventory_repo.count_available(product.id)

    # ── Enough balance + stock → buy immediately ──────────────
    if wallet.balance >= product.price and stock > 0:
        locked_item = await inventory_repo.lock_available_item(product.id)
        if locked_item is None:
            await callback.answer("😔 Out of stock!", show_alert=True)
            return

        deducted = await wallet_repo.deduct_balance(
            wallet_id=wallet.id,
            amount=product.price,
        )
        if not deducted:
            await callback.message.edit_text(
                "❌ <b>Insufficient Balance</b>\n\n"
                f"Use /deposit to add funds.",
            )
            await callback.answer("Insufficient balance!", show_alert=True)
            raise ValueError("Insufficient balance — triggering rollback")

        await inventory_repo.mark_as_sold(
            inventory_id=locked_item.id,
            buyer_id=db_user.id,
        )
        order, _ = await order_repo.create_full_order(
            user_id=db_user.id,
            product_id=product.id,
            inventory_id=locked_item.id,
            price=product.price,
        )

        logger.info(
            "Checkout purchase completed",
            order_id=order.id,
            product_id=product.id,
            user_id=db_user.id,
        )

        new_balance = wallet.balance - product.price
        await callback.message.edit_text(
            f"🎉 <b>Purchase Successful!</b>\n\n"
            f"<b>Product:</b>  {product.name}\n"
            f"<b>Price:</b>    ${product.price:.2f}\n"
            f"<b>Order:</b>    #{order.id}\n"
        )
        await callback.message.answer(
            f"🔐 <b>Your Digital Code</b>\n\n"
            f"<b>Product:</b> {product.name}\n"
            f"<b>Order:</b>   #{order.id}\n\n"
            f"<tg-spoiler>{locked_item.data}</tg-spoiler>\n\n"
            f"<i>⚠️ Save this code — it will not be shown again.</i>\n"
            f"<i>💰 Remaining balance: ${new_balance:.2f}</i>",
        )
        await callback.answer("✅ Purchase complete!")
        return

    # ── Not enough balance → show how to deposit ─────────────
    builder = InlineKeyboardBuilder()
    for key, info in PAYMENT_INFO.items():
        builder.row(
            InlineKeyboardButton(
                text=info["label"],
                callback_data=f"checkout_pay_method:{product.id}:{key}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Back",
            callback_data=CheckoutCallback(
                action=CheckoutAction.PAY,
                product_id=product.id,
            ).pack(),
        ),
    )

    shortage = product.price - wallet.balance
    await callback.message.edit_text(
        f"💳 <b>Top Up Your Balance</b>\n\n"
        f"<b>Product price:</b>  ${product.price:.2f}\n"
        f"<b>Your balance:</b>   ${wallet.balance:.2f}\n"
        f"<b>You need:</b>       ${shortage:.2f} more\n\n"
        f"Choose a payment method to top up, then use /deposit to submit your TXID:",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("checkout_pay_method:"))
async def on_pay_method_info(callback: CallbackQuery) -> None:
    """Show the payment address/UID for the selected method."""
    parts = callback.data.split(":")
    # Format: checkout_pay_method:<product_id>:<method_key>
    product_id = int(parts[1])
    method_key = parts[2]
    info = PAYMENT_INFO.get(method_key)

    if not info:
        await callback.answer("Unknown method.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔙 Back to Checkout", callback_data=f"back_checkout:{product_id}"),
    )

    await callback.message.edit_text(
        f"{info['label']}\n\n"
        f"<b>Send to:</b>\n<code>{info['value']}</code>\n\n"
        f"ℹ️ {info['note']}\n\n"
        f"After sending funds, use /deposit to submit your transaction ID.\n"
        f"Once approved, come back to /shop to purchase.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("back_checkout:"))
async def on_back_to_checkout(
    callback: CallbackQuery,
    product_repo: ProductRepository,
    inventory_repo: InventoryRepository,
    user_repo: UserRepository,
    wallet_repo: WalletRepository,
) -> None:
    """Return user to the checkout screen."""
    product_id = int(callback.data.split(":")[1])
    product = await product_repo.get_by_id(product_id)
    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return

    db_user = await user_repo.get_by_telegram_id(callback.from_user.id)
    balance = 0.0
    if db_user:
        balance = await wallet_repo.get_balance(user_id=db_user.id)
    stock = await inventory_repo.count_available(product_id)

    await callback.message.edit_text(
        f"🛒 <b>Checkout</b>\n\n"
        f"<b>Product:</b>  {product.name}\n"
        f"<b>Price:</b>    ${product.price:.2f}\n"
        f"<b>Stock:</b>    {stock} unit(s)\n"
        f"<b>Balance:</b>  ${balance:.2f}\n\n"
        f"Choose how you'd like to proceed:",
        reply_markup=_checkout_keyboard(product.id),
    )
    await callback.answer()


# ═════════════════════════════════════════════════════════════════════════════
#  Branch B — PROMO CODE
# ═════════════════════════════════════════════════════════════════════════════

@router.callback_query(CheckoutCallback.filter(F.action == CheckoutAction.PROMO))
async def on_checkout_promo(
    callback: CallbackQuery,
    callback_data: CheckoutCallback,
    state: FSMContext,
    product_repo: ProductRepository,
) -> None:
    """User chose 'Use Promo Code' — store product_id in state and ask for code."""
    product = await product_repo.get_by_id(callback_data.product_id)
    if product is None:
        await callback.answer("⚠️ Product not found.", show_alert=True)
        return

    await state.set_state(CheckoutForm.waiting_for_promo_code)
    await state.update_data(checkout_product_id=callback_data.product_id)

    await callback.message.edit_text(
        f"🎁 <b>Enter Your Promo Code</b>\n\n"
        f"Product: <b>{product.name}</b>\n\n"
        f"Type and send your promo code below.\n"
        f"It will be submitted to an admin for verification.\n\n"
        f"Send /cancel to go back."
    )
    await callback.answer()


@router.message(CheckoutForm.waiting_for_promo_code, Command("cancel"))
async def cancel_promo_entry_cmd(message: Message, state: FSMContext) -> None:
    """Cancel the promo code entry via /cancel command."""
    await state.clear()
    await message.answer("❌ Cancelled. Use /shop to browse products.")


@router.message(CheckoutForm.waiting_for_promo_code, F.text & ~F.text.startswith("/"))
async def process_promo_code(
    message: Message,
    state: FSMContext,
    bot: Bot,
    user_repo: UserRepository,
    product_repo: ProductRepository,
    promo_repo: PromoCodeRepository,
) -> None:
    """
    Receive the promo code typed by the user.

    1. Validate user + product still exist
    2. Create a PromoOrderRequest (PENDING)
    3. Notify admin(s) with Approve/Reject buttons
    4. Confirm to user that code is under review
    """
    code_input = message.text.strip()

    if len(code_input) < 2:
        await message.answer("⚠️ Code too short. Please try again.")
        return

    fsm_data = await state.get_data()
    product_id = fsm_data.get("checkout_product_id")
    if not product_id:
        await state.clear()
        await message.answer("⚠️ Session expired. Please use /shop again.")
        return

    db_user = await user_repo.get_by_telegram_id(message.from_user.id)
    if not db_user:
        await state.clear()
        await message.answer("⚠️ Please /start first.")
        return

    product = await product_repo.get_by_id(product_id)
    if not product:
        await state.clear()
        await message.answer("⚠️ Product no longer available.")
        return

    # ── Create the pending request ────────────────────────────
    request = await promo_repo.create_request(
        user_id=db_user.id,
        product_id=product_id,
        promo_code=code_input,
    )

    await state.clear()

    # ── Confirm to user ───────────────────────────────────────
    await message.answer(
        f"✅ <b>Promo Code Submitted!</b>\n\n"
        f"<b>Code:</b>     <code>{code_input.upper()}</code>\n"
        f"<b>Product:</b>  {product.name}\n"
        f"<b>Status:</b>   ⏳ Awaiting admin verification\n\n"
        f"You'll be notified once an admin reviews your code.\n"
        f"This usually takes a few minutes.",
        parse_mode="HTML",
    )

    # ── Build admin notification ──────────────────────────────
    approve_reject_kb = InlineKeyboardBuilder()
    approve_reject_kb.row(
        InlineKeyboardButton(
            text="✅ Approve",
            callback_data=PromoOrderCallback(
                action=PromoOrderAction.APPROVE,
                request_id=request.id,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Reject",
            callback_data=PromoOrderCallback(
                action=PromoOrderAction.REJECT,
                request_id=request.id,
            ).pack(),
        ),
    )

    admin_text = (
        f"🎁 <b>Promo Code Checkout Request</b>\n\n"
        f"<b>Request ID:</b>  #{request.id}\n"
        f"<b>User:</b>        {db_user.first_name} (@{db_user.username or '—'})\n"
        f"<b>Telegram ID:</b> <code>{db_user.telegram_id}</code>\n"
        f"<b>Product:</b>     {product.name}\n"
        f"<b>Price:</b>       ${product.price:.2f}\n"
        f"<b>Code Entered:</b> <code>{code_input.upper()}</code>\n"
        f"<b>Status:</b>      ⏳ Pending"
    )

    for admin_id in settings.bot.admin_ids:
        try:
            sent = await bot.send_message(
                admin_id,
                admin_text,
                parse_mode="HTML",
                reply_markup=approve_reject_kb.as_markup(),
            )
            # Use the SAME injected session (promo_repo) — it can see the
            # flushed-but-uncommitted request row within the same transaction.
            # A separate session would NOT see it (READ COMMITTED isolation).
            await promo_repo.set_admin_message(
                request_id=request.id,
                admin_chat_id=admin_id,
                admin_message_id=sent.message_id,
            )
        except Exception as e:
            logger.warning("Failed to notify admin about promo request", error=str(e))


@router.message(CheckoutForm.waiting_for_promo_code, F.text.startswith("/cancel"))
async def cancel_promo_entry(message: Message, state: FSMContext) -> None:
    """Cancel the promo code entry."""
    await state.clear()
    await message.answer("❌ Cancelled. Use /shop to browse products.")


# ═════════════════════════════════════════════════════════════════════════════
#  ADMIN — Approve / Reject promo-code checkout requests
# ═════════════════════════════════════════════════════════════════════════════

@router.callback_query(PromoOrderCallback.filter())
async def handle_promo_order_decision(
    callback: CallbackQuery,
    callback_data: PromoOrderCallback,
    bot: Bot,
) -> None:
    """
    Approve or Reject a promo-code order request.

    APPROVE flow (atomic in one session):
      1. Read request (no writes)
      2. In ONE transaction: flip status + lock inventory + mark sold + create order
         → If any step fails → everything rolls back → status stays PENDING → admin can retry
      3. Send delivery message outside transaction

    REJECT flow:
      1. Read request + flip status (atomic)
      2. Notify user
    """
    if callback.from_user.id not in settings.bot.admin_ids:
        await callback.answer("🚫 You are not an admin.", show_alert=True)
        return

    request_id = callback_data.request_id
    action = callback_data.action
    status_text = "✅ <b>APPROVED</b>" if action == PromoOrderAction.APPROVE else "❌ <b>REJECTED</b>"

    try:
        # ── SESSION 1: read request primitives (READ ONLY, no writes) ──
        user_id = product_id = telegram_id = None
        promo_code = product_name = ""

        async with async_session_factory() as s1:
            req = await PromoCodeRepository(s1).get_request(request_id)
            if req is None:
                await callback.answer("⚠️ Request not found.", show_alert=True)
                return
            if req.status != PromoOrderStatus.PENDING:
                await callback.answer(
                    f"⚠️ Already {req.status.value.lower()}.", show_alert=True
                )
                return
            user_id      = req.user_id
            product_id   = req.product_id
            promo_code   = req.promo_code
            product_name = req.product.name if req.product else "N/A"

        # ── SESSION 2: fetch user's telegram_id ───────────────────────
        async with async_session_factory() as s2:
            u = await UserRepository(s2).get_by_id(user_id)
            telegram_id = u.telegram_id if u else None

        # ══════════════════════════════════════════════════════════════
        #  APPROVE — all writes in ONE atomic transaction
        #  If lock_available_item fails → everything rolls back
        #  → status stays PENDING → admin can safely retry
        # ══════════════════════════════════════════════════════════════
        if action == PromoOrderAction.APPROVE:
            inventory_code: str | None = None
            order_id: int | None = None

            async with async_session_factory() as s3:
                async with s3.begin():
                    promo_repo3 = PromoCodeRepository(s3)
                    inv_repo    = InventoryRepository(s3)
                    order_repo  = OrderRepository(s3)

                    # Flip status INSIDE the same transaction as inventory lock
                    ok = await promo_repo3.approve_request(
                        request_id, note=f"Approved by {callback.from_user.id}"
                    )
                    if not ok:
                        await callback.answer("⚠️ Already processed.", show_alert=True)
                        return

                    # Lock one inventory item — FOR UPDATE SKIP LOCKED OF Inventory
                    item = await inv_repo.lock_available_item(product_id)
                    if item is None:
                        # Raise so the transaction ROLLS BACK (status reverts to PENDING)
                        raise RuntimeError("OUT_OF_STOCK")

                    inventory_code = item.data
                    await inv_repo.mark_as_sold(inventory_id=item.id, buyer_id=user_id)
                    order, _ = await order_repo.create_full_order(
                        user_id=user_id,
                        product_id=product_id,
                        inventory_id=item.id,
                        price=0.0,
                    )
                    order_id = order.id

            logger.info(
                "Promo order approved and fulfilled",
                request_id=request_id, order_id=order_id,
                user_id=user_id, admin_id=callback.from_user.id,
            )

            # Deliver code OUTSIDE the transaction
            if telegram_id and inventory_code:
                try:
                    await bot.send_message(
                        telegram_id,
                        f"✅ <b>Promo Code Approved!</b>\n\n"
                        f"<b>Product:</b> {product_name}\n"
                        f"<b>Order #:</b> {order_id}\n\n"
                        f"🔐 <b>Your Digital Code:</b>\n"
                        f"<tg-spoiler>{inventory_code}</tg-spoiler>\n\n"
                        f"<i>⚠️ Save this — it won't be shown again.</i>",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("Code delivery failed", error=str(e))

        # ══════════════════════════════════════════════════════════════
        #  REJECT — atomic status flip + notify user
        # ══════════════════════════════════════════════════════════════
        else:
            async with async_session_factory() as s3:
                async with s3.begin():
                    ok = await PromoCodeRepository(s3).reject_request(
                        request_id, note=f"Rejected by {callback.from_user.id}"
                    )
                    if not ok:
                        await callback.answer("⚠️ Already processed.", show_alert=True)
                        return

            logger.info(
                "Promo order rejected",
                request_id=request_id, user_id=user_id, admin_id=callback.from_user.id,
            )

            if telegram_id:
                try:
                    kb = InlineKeyboardBuilder()
                    kb.row(
                        InlineKeyboardButton(
                            text="💳 Pay",
                            callback_data=CheckoutCallback(
                                action=CheckoutAction.PAY, product_id=product_id
                            ).pack(),
                        ),
                        InlineKeyboardButton(
                            text="🎁 Try Another Code",
                            callback_data=CheckoutCallback(
                                action=CheckoutAction.PROMO, product_id=product_id
                            ).pack(),
                        ),
                    )
                    await bot.send_message(
                        telegram_id,
                        f"❌ <b>Promo Code Rejected</b>\n\n"
                        f"<b>Code:</b>    <code>{promo_code}</code>\n"
                        f"<b>Product:</b> {product_name}\n\n"
                        f"Code was invalid or has expired.\n"
                        f"You can try a different code or pay directly:",
                        parse_mode="HTML",
                        reply_markup=kb.as_markup(),
                    )
                except Exception as e:
                    logger.warning("Reject notify failed", error=str(e))

        # ── Edit admin message to show outcome ─────────────────────
        try:
            await callback.message.edit_text(
                f"{callback.message.html_text}\n\n"
                f"─────────────────────\n"
                f"{status_text} by <code>{callback.from_user.id}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        await callback.answer(
            "Approved ✅" if action == PromoOrderAction.APPROVE else "Rejected ❌"
        )

    except RuntimeError as e:
        if "OUT_OF_STOCK" in str(e):
            # Status was rolled back — admin can retry after restocking
            if telegram_id:
                try:
                    await bot.send_message(
                        telegram_id,
                        f"✅ <b>Promo Approved!</b>\n\n"
                        f"<b>Product:</b> {product_name}\n\n"
                        f"⚠️ Currently out of stock. You'll be notified when restocked.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            await callback.answer(
                "⚠️ Approved but OUT OF STOCK — please add stock and retry.",
                show_alert=True,
            )
        else:
            logger.error("Promo decision error", error=str(e), request_id=request_id)
            try:
                await callback.answer(f"❌ Error: {str(e)[:150]}", show_alert=True)
            except Exception:
                pass

    except Exception as e:
        logger.error("Promo decision error", error=str(e), request_id=request_id)
        try:
            await callback.answer(f"❌ Error: {str(e)[:150]}", show_alert=True)
        except Exception:
            pass


