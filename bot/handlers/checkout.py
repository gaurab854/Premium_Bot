"""
Checkout handler — handles wallet deduction, inventory assignment, and order creation.
Also handles GMAIL specific flows.
"""

from __future__ import annotations

import re
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
    GmailInviteCallback,
)
from bot.callbacks.purchase import ProductAction, ProductCallback
from bot.states.user import CheckoutForm
from config import settings
from database import async_session_factory
from database.repositories.inventory import InventoryRepository
from database.repositories.order import OrderRepository
from database.repositories.product import ProductRepository
from database.repositories.user import UserRepository
from database.repositories.wallet import WalletRepository

logger = structlog.get_logger()

router = Router(name="checkout")




@router.callback_query(ProductCallback.filter(F.action == ProductAction.BUY))
async def on_buy_clicked(
    callback: CallbackQuery,
    callback_data: ProductCallback,
    product_repo: ProductRepository,
    user_repo: UserRepository,
    wallet_repo: WalletRepository,
    inventory_repo: InventoryRepository,
    order_repo: OrderRepository,
    state: FSMContext,
) -> None:
    """
    User chose 'Buy'.
    If they have enough balance → process the purchase immediately.
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
            "Purchase completed",
            order_id=order.id,
            product_id=product.id,
            user_id=db_user.id,
        )

        new_balance = wallet.balance - product.price

        # Check Category
        is_gmail        = product.category and "GMAIL" in product.category.upper()
        is_full_warranty = product.category and product.category.upper() in (
            "FULL WARRANTY", "FULL_WARRANTY", "FULLWARRANTY"
        )

        if is_gmail:
            await callback.message.edit_text(
                f"🎉 <b>Purchase Successful!</b>\n\n"
                f"<b>Product:</b>  {product.name}\n"
                f"<b>Price:</b>    ${product.price:.2f}\n"
                f"<b>Order:</b>    #{order.id}\n"
            )
            await callback.message.answer(
                f"📧 <b>Gmail Required</b>\n\n"
                f"To receive your invitation for <b>{product.name}</b>, please reply to this message with your Gmail address.\n\n"
                f"<i>💰 Remaining balance: ${new_balance:.2f}</i>"
            )
            await state.set_state(CheckoutForm.waiting_for_gmail)
            await state.update_data(checkout_order_id=order.id, checkout_product_name=product.name)
            await callback.answer("✅ Purchase complete! Please provide your Gmail.")

        elif is_full_warranty:
            await callback.message.edit_text(
                f"🎉 <b>Purchase Successful!</b>\n\n"
                f"<b>Product:</b>  {product.name}\n"
                f"<b>Price:</b>    ${product.price:.2f}\n"
                f"<b>Order:</b>    #{order.id}\n"
                f"🛡️ <b>Full Warranty</b> included!"
            )
            # Build contact button
            kb = InlineKeyboardBuilder()
            kb.row(
                InlineKeyboardButton(
                    text="📞 Contact Support for Replacement",
                    url="https://t.me/Randompliw",
                )
            )
            await callback.message.answer(
                f"🔐 <b>Your Digital Code</b>\n\n"
                f"<b>Product:</b> {product.name}\n"
                f"<b>Order:</b>   #{order.id}\n\n"
                f"<tg-spoiler>{locked_item.data}</tg-spoiler>\n\n"
                f"<i>⚠️ Save this code — it will not be shown again.</i>\n"
                f"<i>💰 Remaining balance: ${new_balance:.2f}</i>\n\n"
                f"🛡️ This product comes with <b>Full Warranty</b>.\n"
                f"If you face any issues, use /contact or tap the button below.",
                reply_markup=kb.as_markup(),
            )
            await callback.answer("✅ Purchase complete!")

        else:
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
    builder.row(
        InlineKeyboardButton(
            text="📥 Deposit via Bybit UID",
            callback_data=f"checkout_pay_method:{product.id}:bybit",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📥 Deposit via BEP-20 (USDT)",
            callback_data=f"checkout_pay_method:{product.id}:bep20",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📥 Deposit via Plasma (USDT)",
            callback_data=f"checkout_pay_method:{product.id}:plasma",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Back to Shop",
            callback_data="back_to_shop",
        ),
    )

    shortage = product.price - wallet.balance
    await callback.message.edit_text(
        f"💳 <b>Insufficient Balance</b>\n\n"
        f"<b>Product price:</b>  ${product.price:.2f}\n"
        f"<b>Your balance:</b>   ${wallet.balance:.2f}\n"
        f"<b>You need:</b>       ${shortage:.2f} more\n\n"
        f"👇 Tap a payment method below to deposit funds.\n"
        f"You'll be guided through the process step by step.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("checkout_pay_method:"))
async def on_pay_method_info(callback: CallbackQuery, state: FSMContext) -> None:
    """Show the payment address/UID for the selected method and start deposit FSM."""
    from bot.states.user import DepositForm

    parts = callback.data.split(":")
    # Format: checkout_pay_method:<product_id>:<method_key>
    product_id = int(parts[1])
    method_key = parts[2]
    info = PAYMENT_INFO.get(method_key)

    if not info:
        await callback.answer("Unknown method.", show_alert=True)
        return

    # Start the deposit FSM so the user can paste their TXID immediately
    await state.set_state(DepositForm.waiting_for_amount)
    await state.update_data(payment_method=method_key)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔙 Back", callback_data=f"back_checkout:{product_id}"),
    )

    await callback.message.edit_text(
        f"{info['label']}\n\n"
        f"<b>Send to:</b>\n<code>{info['value']}</code>\n\n"
        f"ℹ️ {info['note']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>How much are you depositing? (in USD)</b>\n\n"
        f"<i>Reply with the amount, e.g. 25.00</i>\n"
        f"<i>Then you'll be asked for your Transaction Hash.</i>",
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
    """Return user to the payment options screen."""
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
            text="🔙 Back to Shop",
            callback_data="back_to_shop",
        ),
    )

    shortage = product.price - balance
    await callback.message.edit_text(
        f"💳 <b>Top Up Your Balance</b>\n\n"
        f"<b>Product price:</b>  ${product.price:.2f}\n"
        f"<b>Your balance:</b>   ${balance:.2f}\n"
        f"<b>You need:</b>       ${shortage:.2f} more\n\n"
        f"Choose a payment method to top up, then use /deposit to submit your TXID:",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


# ═════════════════════════════════════════════════════════════════════════════
#  GMAIL INVITE FLOW
# ═════════════════════════════════════════════════════════════════════════════

@router.message(CheckoutForm.waiting_for_gmail, F.text)
async def on_gmail_provided(message: Message, state: FSMContext, bot: Bot) -> None:
    """User provides their Gmail address."""
    gmail = message.text.strip()
    
    # Simple email validation, but specifically checking for @gmail.com or @googlemail.com
    if not re.match(r"^[a-zA-Z0-9._%+-]+@(gmail|googlemail)\.com$", gmail.lower()):
        await message.answer("⚠️ Please provide a valid Gmail address (e.g., example@gmail.com).")
        return

    data = await state.get_data()
    order_id = data.get("checkout_order_id")
    product_name = data.get("checkout_product_name", "Unknown Product")

    await state.clear()

    await message.answer(
        "✅ <b>Message sent to admin!</b>\n\n"
        "Your Gmail address has been securely forwarded to the admin.\n"
        "Please wait while they add you to the group/family. You will receive a notification here once it is done."
    )

    # Notify admins
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="✅ Sent",
            callback_data=GmailInviteCallback(order_id=order_id, user_id=message.from_user.id).pack(),
        )
    )

    for admin_id in settings.bot.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                f"🚨 <b>New GMAIL Order Action Required!</b>\n\n"
                f"<b>Order:</b> #{order_id}\n"
                f"<b>Product:</b> {product_name}\n"
                f"<b>User:</b> @{message.from_user.username or message.from_user.id}\n"
                f"<b>Gmail:</b> <code>{gmail}</code>\n\n"
                f"Please manually add this user and click [Sent] below.",
                reply_markup=kb.as_markup()
            )
        except Exception as e:
            logger.warning("Failed to notify admin of Gmail order", admin_id=admin_id, error=str(e))


@router.callback_query(GmailInviteCallback.filter())
async def on_admin_gmail_invite_sent(
    callback: CallbackQuery,
    callback_data: GmailInviteCallback,
    bot: Bot
) -> None:
    """Admin clicks [Sent] after manually processing the Gmail invite."""
    if callback.from_user.id not in settings.bot.admin_ids:
        await callback.answer("🚫 You are not an admin.", show_alert=True)
        return

    order_id = callback_data.order_id
    user_id = callback_data.user_id

    # Update admin message
    try:
        await callback.message.edit_text(
            f"{callback.message.html_text}\n\n"
            f"─────────────────────\n"
            f"✅ <b>Invitation Sent</b> by <code>{callback.from_user.id}</code>",
            parse_mode="HTML"
        )
    except Exception:
        pass

    # Notify user
    try:
        await bot.send_message(
            user_id,
            f"🎉 <b>Invitation Sent!</b>\n\n"
            f"Check your email and accept the invitation for Order #{order_id}.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning("Failed to notify user that invite was sent", user_id=user_id, error=str(e))

    await callback.answer("Marked as Sent ✅")
