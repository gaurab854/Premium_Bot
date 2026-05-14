"""
Purchase handler — the complete buy flow for digital goods.

Flow:
    /shop        → browse products (inline buttons)
    [View]       → product detail + stock count + [Buy] button
    [Buy]        → check balance → lock inventory → deduct balance
                   → mark sold → create order → send code

Every step that mutates data runs on the SAME session injected by
``DatabaseMiddleware``, so the entire purchase is ONE atomic DB
transaction:
    • lock_available_item  (FOR UPDATE SKIP LOCKED)
    • deduct_balance       (WHERE balance >= price)
    • mark_as_sold
    • create_full_order
    → all committed together by the middleware on success
    → all rolled back together on any failure
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks.purchase import CategoryCallback, ProductAction, ProductCallback
from database.repositories.inventory import InventoryRepository
from database.repositories.order import OrderRepository
from database.repositories.product import ProductRepository
from database.repositories.user import UserRepository
from database.repositories.wallet import WalletRepository

logger = structlog.get_logger()

router = Router(name="purchase")


# ═════════════════════════════════════════════════════════════
#  /shop — browse the product catalogue
# ═════════════════════════════════════════════════════════════

@router.message(Command("shop"))
async def cmd_shop(
    message: Message,
    product_repo: ProductRepository,
    inventory_repo: InventoryRepository,
) -> None:
    """
    Display all available products with stock counts.

    Each product gets a [View] inline button that shows full details.
    """
    products = await product_repo.get_available()

    if not products:
        await message.answer(
            "🏪 <b>Shop</b>\n\n"
            "No products available at the moment. Check back later!",
        )
        return

    builder = InlineKeyboardBuilder()
    lines: list[str] = ["🏪 <b>Available Products</b>\n"]

    for p in products:
        stock = await inventory_repo.count_available(p.id)
        stock_label = f"({stock} in stock)" if stock > 0 else "(OUT OF STOCK)"

        lines.append(
            f"• <b>{p.name}</b> — ${p.price:.2f}  {stock_label}"
        )
        builder.row(
            InlineKeyboardButton(
                text=f"🔍 {p.name}",
                callback_data=ProductCallback(
                    action=ProductAction.VIEW,
                    product_id=p.id,
                ).pack(),
            ),
        )

    lines.append("\n<i>Tap a product to view details and purchase.</i>")
    await message.answer(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
    )


# ═════════════════════════════════════════════════════════════
#  [View] — show product details + Buy button
# ═════════════════════════════════════════════════════════════

@router.callback_query(
    ProductCallback.filter(F.action == ProductAction.VIEW),
)
async def on_product_view(
    callback: CallbackQuery,
    callback_data: ProductCallback,
    product_repo: ProductRepository,
    inventory_repo: InventoryRepository,
    wallet_repo: WalletRepository,
    user_repo: UserRepository,
) -> None:
    """Show full product details with a [Buy] confirmation button."""
    product = await product_repo.get_by_id(callback_data.product_id)
    if product is None or not product.is_available:
        await callback.answer("⚠️ Product not found.", show_alert=True)
        return

    stock = await inventory_repo.count_available(product.id)

    # Get the user's current balance for context
    db_user = await user_repo.get_by_telegram_id(callback.from_user.id)
    balance = 0.0
    if db_user:
        balance = await wallet_repo.get_balance(user_id=db_user.id)

    # ── Build detail text ─────────────────────────────────────
    description = product.description or "No description available."
    can_afford = "✅ You can afford this" if balance >= product.price else "❌ Insufficient balance"
    stock_text = f"📦 {stock} in stock" if stock > 0 else "🚫 OUT OF STOCK"

    text = (
        f"🏷️ <b>{product.name}</b>\n\n"
        f"{description}\n\n"
        f"<b>Price:</b>    ${product.price:.2f}\n"
        f"<b>Stock:</b>    {stock_text}\n"
        f"<b>Balance:</b>  ${balance:.2f}\n"
        f"<b>Status:</b>   {can_afford}\n"
    )

    # ── Build inline keyboard ─────────────────────────────────
    builder = InlineKeyboardBuilder()

    if stock > 0 and balance >= product.price:
        builder.row(
            InlineKeyboardButton(
                text=f"💳 Buy for ${product.price:.2f}",
                callback_data=ProductCallback(
                    action=ProductAction.BUY,
                    product_id=product.id,
                ).pack(),
            ),
        )

    builder.row(
        InlineKeyboardButton(
            text="🔙 Back to Shop",
            callback_data="back_to_shop",
        ),
    )

    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


# ═════════════════════════════════════════════════════════════
#  "Back to Shop" — return to the product list
# ═════════════════════════════════════════════════════════════

@router.callback_query(lambda c: c.data == "back_to_shop")
async def on_back_to_shop(
    callback: CallbackQuery,
    product_repo: ProductRepository,
    inventory_repo: InventoryRepository,
) -> None:
    """Re-render the shop catalogue inline (avoids /shop re-send)."""
    products = await product_repo.get_available()

    if not products:
        await callback.message.edit_text(
            "🏪 <b>Shop</b>\n\nNo products available at the moment."
        )
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    lines: list[str] = ["🏪 <b>Available Products</b>\n"]

    for p in products:
        stock = await inventory_repo.count_available(p.id)
        stock_label = f"({stock} in stock)" if stock > 0 else "(OUT OF STOCK)"
        lines.append(f"• <b>{p.name}</b> — ${p.price:.2f}  {stock_label}")
        builder.row(
            InlineKeyboardButton(
                text=f"🔍 {p.name}",
                callback_data=ProductCallback(
                    action=ProductAction.VIEW,
                    product_id=p.id,
                ).pack(),
            ),
        )

    lines.append("\n<i>Tap a product to view details and purchase.</i>")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


# ═════════════════════════════════════════════════════════════
#  [Buy] — THE PURCHASE FLOW (the critical path)
# ═════════════════════════════════════════════════════════════
#
#  This handler is the single most important function in the bot.
#  It performs FOUR database mutations in one atomic transaction:
#
#    1. lock_available_item()  → SELECT … FOR UPDATE SKIP LOCKED
#    2. deduct_balance()       → UPDATE wallets SET balance = balance - price
#                                WHERE balance >= price
#    3. mark_as_sold()         → UPDATE inventory SET is_sold = true
#    4. create_full_order()    → INSERT orders + INSERT order_items
#
#  All four run on the SAME session.  The middleware auto-commits
#  on success and auto-rollbacks on any exception.
#
#  If ANY step fails, the entire transaction rolls back:
#    • Locked inventory is released (goes back to available pool)
#    • Wallet balance is untouched
#    • No orphan order records
#
# ═════════════════════════════════════════════════════════════

@router.callback_query(
    ProductCallback.filter(F.action == ProductAction.BUY),
)
async def on_product_buy(
    callback: CallbackQuery,
    callback_data: ProductCallback,
    user_repo: UserRepository,
    wallet_repo: WalletRepository,
    inventory_repo: InventoryRepository,
    product_repo: ProductRepository,
    order_repo: OrderRepository,
) -> None:
    """
    Execute the full purchase flow:
      1. Validate product exists & is available
      2. Validate user is registered
      3. Lock an inventory item (FOR UPDATE SKIP LOCKED)
      4. Deduct wallet balance (atomic WHERE guard)
      5. Mark inventory as sold
      6. Create order + order_item records
      7. Send the digital code to the user via spoiler text

    The entire sequence runs on a single DB session/transaction.
    """
    product_id = callback_data.product_id

    # ── 1. Validate product ───────────────────────────────────
    product = await product_repo.get_by_id(product_id)
    if product is None or not product.is_available:
        await callback.answer("⚠️ Product is no longer available.", show_alert=True)
        return

    # ── 2. Validate user ──────────────────────────────────────
    db_user = await user_repo.get_by_telegram_id(callback.from_user.id)
    if not db_user:
        await callback.answer("⚠️ Please /start first to register.", show_alert=True)
        return

    wallet = await wallet_repo.get_or_create(user_id=db_user.id)

    # ── Quick balance pre-check (non-authoritative) ───────────
    if wallet.balance < product.price:
        await callback.answer(
            f"❌ Insufficient balance: ${wallet.balance:.2f} < ${product.price:.2f}",
            show_alert=True,
        )
        return

    # ── 3. Lock an inventory item ─────────────────────────────
    #    SELECT … FOR UPDATE SKIP LOCKED
    #    Returns None if no unsold & unlocked rows remain.
    locked_item = await inventory_repo.lock_available_item(product_id)

    if locked_item is None:
        await callback.message.edit_text(
            f"😔 <b>Out of Stock</b>\n\n"
            f"<b>{product.name}</b> is currently sold out.\n"
            f"Please check back later!",
        )
        await callback.answer("Out of stock!", show_alert=True)
        return

    # ── 4. Deduct wallet balance (atomic) ─────────────────────
    #    UPDATE wallets SET balance = balance - price
    #    WHERE id = :wid AND balance >= :price
    #    Returns False if insufficient funds (race-safe).
    deducted = await wallet_repo.deduct_balance(
        wallet_id=wallet.id,
        amount=product.price,
    )

    if not deducted:
        # Balance was insufficient at the DB level
        # (could happen if another purchase was processed concurrently)
        # The transaction will roll back → inventory lock is released.
        await callback.message.edit_text(
            f"❌ <b>Insufficient Balance</b>\n\n"
            f"Your balance is less than ${product.price:.2f}.\n"
            f"Use /deposit to add funds.",
        )
        await callback.answer("Insufficient balance!", show_alert=True)
        # Raise to trigger rollback and release the inventory lock
        raise ValueError("Insufficient balance — triggering rollback")

    # ── 5. Mark inventory as sold ─────────────────────────────
    await inventory_repo.mark_as_sold(
        inventory_id=locked_item.id,
        buyer_id=db_user.id,
    )

    # ── 6. Create order + order_item ──────────────────────────
    order, order_item = await order_repo.create_full_order(
        user_id=db_user.id,
        product_id=product.id,
        inventory_id=locked_item.id,
        price=product.price,
        quantity=1,
    )

    logger.info(
        "Purchase completed",
        order_id=order.id,
        product_id=product.id,
        product_name=product.name,
        inventory_id=locked_item.id,
        user_id=db_user.id,
        telegram_id=db_user.telegram_id,
        price=product.price,
    )

    # ── 7. Send the digital code ──────────────────────────────
    #    The middleware will COMMIT the transaction after this
    #    handler returns successfully.  Only then is the purchase
    #    truly persisted.

    # Get updated balance (still on the same session, pre-commit)
    new_balance = wallet.balance - product.price  # approximation pre-commit

    # Edit the original message to show purchase complete
    await callback.message.edit_text(
        f"🎉 <b>Purchase Successful!</b>\n\n"
        f"<b>Product:</b>  {product.name}\n"
        f"<b>Price:</b>    ${product.price:.2f}\n"
        f"<b>Order:</b>    #{order.id}\n"
    )

    # Send the code in a SEPARATE message with spoiler formatting
    # so it's hidden in chat previews and notifications
    await callback.message.answer(
        f"🔐 <b>Your Digital Code</b>\n\n"
        f"<b>Product:</b> {product.name}\n"
        f"<b>Order:</b>   #{order.id}\n\n"
        f"<tg-spoiler>{locked_item.data}</tg-spoiler>\n\n"
        f"<i>⚠️ Save this code! It will not be shown again.</i>\n"
        f"<i>💰 Remaining balance: ${new_balance:.2f}</i>",
    )

    await callback.answer("✅ Purchase complete!", show_alert=False)


# ═════════════════════════════════════════════════════════════
#  /orders — view purchase history
# ═════════════════════════════════════════════════════════════

@router.message(Command("orders"))
async def cmd_orders(
    message: Message,
    user_repo: UserRepository,
    order_repo: OrderRepository,
) -> None:
    """Display the user's recent order history."""
    db_user = await user_repo.get_by_telegram_id(message.from_user.id)
    if not db_user:
        await message.answer("⚠️ Please /start first to register.")
        return

    orders = await order_repo.get_by_user(db_user.id, limit=10)

    if not orders:
        await message.answer(
            "📋 <b>Order History</b>\n\n"
            "You haven't made any purchases yet.\n"
            "Use /shop to browse products!",
        )
        return

    lines = ["📋 <b>Your Recent Orders</b>\n"]
    for o in orders:
        status_emoji = {
            "completed": "✅",
            "pending": "⏳",
            "cancelled": "❌",
            "refunded": "🔄",
        }.get(o.status.value, "❓")

        lines.append(
            f"{status_emoji} <b>Order #{o.id}</b> — "
            f"${o.total_amount:.2f} — "
            f"{o.created_at:%Y-%m-%d %H:%M}"
        )

    total_count = await order_repo.count_by_user(db_user.id)
    lines.append(f"\n<i>Showing {len(orders)} of {total_count} total orders.</i>")

    await message.answer("\n".join(lines))
