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
# pyrefly: ignore [missing-import]
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


# ═════════════════════════════════════════════════════════════════════════════
#  NOTE: The [Buy] callback (ProductAction.BUY) is now handled by checkout.py.
#  It intercepts the BUY action and presents a Pay / Promo Code gateway.
# ═════════════════════════════════════════════════════════════════════════════


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
