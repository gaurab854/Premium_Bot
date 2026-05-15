"""
Admin handlers — product management, stats, ban/unban, deposit decisions.

Admin commands:
    /addproduct   — Add a new product (FSM: name → price → category → description → codes)
    /products     — List all products with stock counts
    /addstock     — Add more inventory codes to an existing product
    /delproduct   — Hide/show a product from the catalogue
    /stats        — Bot statistics
    /ban          — Ban a user
    /unban        — Unban a user
    /broadcast    — Send a message to all users

Deposit callbacks:
    ✅ Approve / ❌ Reject   — inline buttons on deposit notifications
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

from bot.callbacks.deposit import DepositAction, DepositCallback
from bot.filters.admin import AdminFilter
from bot.states.user import AddProductForm, AddPromoForm
from config import settings
from database import async_session_factory
from database.repositories.deposit import DepositRepository
from database.repositories.inventory import InventoryRepository
from database.repositories.product import ProductRepository
from database.repositories.promo_code import PromoCodeRepository
from database.repositories.user import UserRepository
from database.repositories.wallet import WalletRepository

logger = structlog.get_logger()

router = Router(name="admin")
router.message.filter(AdminFilter())


# ═════════════════════════════════════════════════════════════════════════════
#  /addproduct — FSM flow to add a new product
# ═════════════════════════════════════════════════════════════════════════════

@router.message(Command("addproduct"))
async def cmd_addproduct(message: Message, state: FSMContext) -> None:
    """Start the add-product flow — ask for the product name."""
    await state.set_state(AddProductForm.waiting_for_name)
    await message.answer(
        "🛍️ <b>Add New Product</b>\n\n"
        "<b>Step 1/5</b> — Enter the product <b>name</b>:\n\n"
        "<i>Example: Netflix Premium 1 Month</i>\n\n"
        "Send /cancel to abort."
    )


@router.message(AddProductForm.waiting_for_name, F.text)
async def process_product_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("⚠️ Name is too short. Please enter a proper product name.")
        return
    if len(name) > 256:
        await message.answer("⚠️ Name is too long (max 256 characters).")
        return

    await state.update_data(product_name=name)
    await state.set_state(AddProductForm.waiting_for_price)
    await message.answer(
        f"✅ Name: <b>{name}</b>\n\n"
        "<b>Step 2/5</b> — Enter the <b>price</b> in USD:\n\n"
        "<i>Example: 9.99</i>"
    )


@router.message(AddProductForm.waiting_for_price, F.text)
async def process_product_price(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    try:
        price = float(raw)
    except ValueError:
        await message.answer("⚠️ Invalid price. Enter a number like <code>9.99</code>.")
        return

    if price <= 0:
        await message.answer("⚠️ Price must be greater than zero.")
        return

    await state.update_data(product_price=price)
    await state.set_state(AddProductForm.waiting_for_category)
    await message.answer(
        f"✅ Price: <b>${price:.2f}</b>\n\n"
        "<b>Step 3/5</b> — Enter the <b>category</b>:\n\n"
        "<i>Examples: Streaming, VPN, Software, Gift Cards</i>\n\n"
        "Or send <code>-</code> to skip."
    )


@router.message(AddProductForm.waiting_for_category, F.text)
async def process_product_category(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    category = None if raw == "-" else raw[:128]

    await state.update_data(product_category=category)
    await state.set_state(AddProductForm.waiting_for_description)
    cat_display = category or "None"
    await message.answer(
        f"✅ Category: <b>{cat_display}</b>\n\n"
        "<b>Step 4/5</b> — Enter the <b>description</b>:\n\n"
        "<i>What does the user get? e.g. '1-month Netflix Premium with 4 screens'</i>\n\n"
        "Or send <code>-</code> to skip."
    )


@router.message(AddProductForm.waiting_for_description, F.text)
async def process_product_description(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    description = None if raw == "-" else raw

    await state.update_data(product_description=description)
    await state.set_state(AddProductForm.waiting_for_codes)

    fsm_data = await state.get_data()
    await message.answer(
        f"✅ Description saved.\n\n"
        "<b>Step 5/5</b> — Now enter the <b>inventory codes</b> (the digital goods).\n\n"
        "📋 Send <b>one code per line</b>:\n"
        "<code>CODE1234\nCODE5678\nCODE9012</code>\n\n"
        "<i>Each line = one unit of stock. You can add more later with /addstock.</i>"
    )


@router.message(AddProductForm.waiting_for_codes, F.text)
async def process_product_codes(
    message: Message,
    state: FSMContext,
    bot: Bot,
    product_repo: ProductRepository,
    inventory_repo: InventoryRepository,
) -> None:
    """Save the product and its initial inventory, then announce to channel."""
    raw = message.text.strip()
    codes = [line.strip() for line in raw.splitlines() if line.strip()]

    if not codes:
        await message.answer("⚠️ No codes found. Please send at least one code (one per line).")
        return

    fsm_data = await state.get_data()
    await state.clear()

    # ── Create the product ────────────────────────────────────
    product = await product_repo.create(
        name=fsm_data["product_name"],
        price=fsm_data["product_price"],
        description=fsm_data.get("product_description"),
        category=fsm_data.get("product_category"),
    )

    # ── Bulk-add inventory ────────────────────────────────────
    count = await inventory_repo.add_bulk(product.id, codes)

    logger.info(
        "Product created",
        product_id=product.id,
        name=product.name,
        price=product.price,
        codes_added=count,
    )

    await message.answer(
        f"✅ <b>Product Added Successfully!</b>\n\n"
        f"<b>ID:</b>          #{product.id}\n"
        f"<b>Name:</b>        {product.name}\n"
        f"<b>Price:</b>       ${product.price:.2f}\n"
        f"<b>Category:</b>    {product.category or '—'}\n"
        f"<b>Stock added:</b> {count} code(s)\n\n"
        f"📢 Sending announcement to the channel..."
    )

    # ── Announce to channel ───────────────────────────────────
    if settings.bot.channel_id:
        try:
            desc_text = f"\n📄 {product.description}\n" if product.description else ""
            cat_text = f"🏷️ <b>Category:</b> {product.category}\n" if product.category else ""

            await bot.send_message(
                settings.bot.channel_id,
                f"🆕 <b>New Product Available!</b>\n\n"
                f"🛍️ <b>{product.name}</b>\n"
                f"{desc_text}"
                f"\n{cat_text}"
                f"💰 <b>Price:</b> ${product.price:.2f}\n"
                f"📦 <b>In Stock:</b> {count} unit(s)\n\n"
                f"👉 Use /shop in the bot to purchase!",
            )
            await message.answer("✅ Channel announcement sent!")
        except Exception as e:
            logger.warning("Failed to send channel announcement", error=str(e))
            await message.answer(
                f"⚠️ Product added but channel announcement failed: {e}\n"
                "Make sure the bot is an admin in your channel."
            )
    else:
        await message.answer(
            "ℹ️ No channel configured. Set <code>BOT_CHANNEL_ID</code> in your .env "
            "to enable channel announcements."
        )


# ═════════════════════════════════════════════════════════════════════════════
#  /products — list all products (admin view with stock)
# ═════════════════════════════════════════════════════════════════════════════

@router.message(Command("products"))
async def cmd_products(
    message: Message,
    product_repo: ProductRepository,
    inventory_repo: InventoryRepository,
) -> None:
    """List all products with their current stock counts."""
    products = await product_repo.get_available(limit=50)

    if not products:
        await message.answer("🏪 No products in the catalogue yet.\nUse /addproduct to add one.")
        return

    lines = ["📦 <b>Product Catalogue (Admin View)</b>\n"]
    for p in products:
        stock = await inventory_repo.count_available(p.id)
        sold = await inventory_repo.count_sold(p.id)
        status = "✅" if p.is_available else "🚫"
        lines.append(
            f"{status} <b>#{p.id} — {p.name}</b>\n"
            f"   💰 ${p.price:.2f}  |  📦 {stock} in stock  |  ✅ {sold} sold\n"
            f"   🏷️ {p.category or 'No category'}\n"
        )

    lines.append(
        "\n<i>Commands:\n"
        "/addproduct — Add new product\n"
        "/addstock — Add codes to existing product\n"
        "/delproduct — Hide/show product</i>"
    )
    await message.answer("\n".join(lines))


# ═════════════════════════════════════════════════════════════════════════════
#  /addstock — add more inventory codes to an existing product
# ═════════════════════════════════════════════════════════════════════════════

@router.message(Command("addstock"))
async def cmd_addstock(
    message: Message,
    product_repo: ProductRepository,
    inventory_repo: InventoryRepository,
) -> None:
    """
    Usage: /addstock <product_id>
    Then send codes one per line in the next message.
    """
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        # Show product list to help admin choose
        products = await product_repo.get_available(limit=30)
        if not products:
            await message.answer("No products found. Use /addproduct first.")
            return

        lines = ["📦 <b>Choose a Product ID to add stock to:</b>\n"]
        for p in products:
            stock = await inventory_repo.count_available(p.id)
            lines.append(f"• <code>/addstock {p.id}</code> — <b>{p.name}</b> ({stock} in stock)")
        await message.answer("\n".join(lines))
        return

    product_id = int(args[1].strip())
    product = await product_repo.get_by_id(product_id)
    if not product:
        await message.answer(f"⚠️ Product #{product_id} not found.")
        return

    stock = await inventory_repo.count_available(product_id)
    await message.answer(
        f"📦 <b>Adding stock to: {product.name}</b>\n"
        f"Current stock: {stock} unit(s)\n\n"
        f"Please send the codes now, <b>one per line</b>:\n"
        f"<code>CODE1\nCODE2\nCODE3</code>\n\n"
        f"⚠️ This is a one-time message — make sure your codes are ready."
    )

    # We store product_id in FSM so next message handler can pick it up
    # We use a simple trick: set state data so the fallback below handles it


@router.message(Command("delproduct"))
async def cmd_delproduct(
    message: Message,
    product_repo: ProductRepository,
) -> None:
    """
    Toggle product visibility.
    Usage: /delproduct <product_id>
    """
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("⚠️ Usage: <code>/delproduct &lt;product_id&gt;</code>")
        return

    product_id = int(args[1].strip())
    product = await product_repo.get_by_id(product_id)
    if not product:
        await message.answer(f"⚠️ Product #{product_id} not found.")
        return

    # Toggle availability
    new_status = not product.is_available
    await product_repo.set_available(product_id, available=new_status)

    emoji = "✅" if new_status else "🚫"
    status = "shown" if new_status else "hidden"
    await message.answer(
        f"{emoji} Product <b>#{product_id} — {product.name}</b> is now <b>{status}</b>."
    )


# ═════════════════════════════════════════════════════════════════════════════
#  /stats
# ═════════════════════════════════════════════════════════════════════════════

@router.message(Command("stats"))
async def cmd_stats(
    message: Message,
    user_repo: UserRepository,
    deposit_repo: DepositRepository,
    product_repo: ProductRepository,
    inventory_repo: InventoryRepository,
) -> None:
    """Show bot statistics."""
    active = await user_repo.count_active()
    total = await user_repo.count_all()
    pending = await deposit_repo.count_pending()
    products = await product_repo.get_available(limit=100)

    total_stock = 0
    total_sold = 0
    for p in products:
        total_stock += await inventory_repo.count_available(p.id)
        total_sold += await inventory_repo.count_sold(p.id)

    await message.answer(
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👥 <b>Users</b>\n"
        f"  Total:   {total}\n"
        f"  Active:  {active}\n\n"
        f"📦 <b>Products</b>\n"
        f"  Listed:    {len(products)}\n"
        f"  In Stock:  {total_stock}\n"
        f"  Sold:      {total_sold}\n\n"
        f"💰 <b>Deposits</b>\n"
        f"  Pending:  {pending}\n"
    )


# ═════════════════════════════════════════════════════════════════════════════
#  /ban  /unban
# ═════════════════════════════════════════════════════════════════════════════

@router.message(Command("ban"))
async def cmd_ban(message: Message, user_repo: UserRepository) -> None:
    """Ban a user by Telegram ID. Usage: /ban <telegram_id>"""
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("⚠️ Usage: <code>/ban &lt;telegram_id&gt;</code>")
        return

    target_id = int(args[1].strip())
    await user_repo.set_active(target_id, active=False)
    await message.answer(f"🚫 User <code>{target_id}</code> has been banned.")


@router.message(Command("unban"))
async def cmd_unban(message: Message, user_repo: UserRepository) -> None:
    """Unban a user by Telegram ID. Usage: /unban <telegram_id>"""
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("⚠️ Usage: <code>/unban &lt;telegram_id&gt;</code>")
        return

    target_id = int(args[1].strip())
    await user_repo.set_active(target_id, active=True)
    await message.answer(f"✅ User <code>{target_id}</code> has been unbanned.")


# ═════════════════════════════════════════════════════════════════════════════
#  /cancel — abort any admin FSM flow
# ═════════════════════════════════════════════════════════════════════════════

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Cancelled.")


# ═════════════════════════════════════════════════════════════════════════════
#  Deposit Approve / Reject callbacks
# ═════════════════════════════════════════════════════════════════════════════

@router.callback_query(DepositCallback.filter())
async def handle_deposit_decision(
    callback: CallbackQuery,
    callback_data: DepositCallback,
    bot: Bot,
) -> None:
    """
    Admin presses Approve or Reject on a deposit notification.

    Uses a fresh session with explicit begin() to ensure
    status transition + balance credit are atomic and idempotent.
    """
    if callback.from_user.id not in settings.bot.admin_ids:
        await callback.answer("🚫 You are not an admin.", show_alert=True)
        return

    deposit_id = callback_data.deposit_id
    action = callback_data.action

    async with async_session_factory() as session:
        async with session.begin():
            deposit_repo = DepositRepository(session)
            wallet_repo = WalletRepository(session)
            user_repo = UserRepository(session)

            deposit = await deposit_repo.get_by_id(deposit_id)

            if deposit is None:
                await callback.answer("⚠️ Deposit not found.", show_alert=True)
                return

            if action == DepositAction.APPROVE:
                was_updated = await deposit_repo.approve(
                    deposit_id,
                    note=f"Approved by admin {callback.from_user.id}",
                )

                if not was_updated:
                    await callback.answer(
                        "⚠️ This deposit has already been processed.", show_alert=True
                    )
                    return

                wallet = await wallet_repo.get_or_create(user_id=deposit.user_id)
                await wallet_repo.add_balance(wallet_id=wallet.id, amount=deposit.amount)

                logger.info(
                    "Deposit approved",
                    deposit_id=deposit_id,
                    user_id=deposit.user_id,
                    amount=deposit.amount,
                    admin_id=callback.from_user.id,
                )

            elif action == DepositAction.REJECT:
                was_updated = await deposit_repo.reject(
                    deposit_id,
                    note=f"Rejected by admin {callback.from_user.id}",
                )

                if not was_updated:
                    await callback.answer(
                        "⚠️ This deposit has already been processed.", show_alert=True
                    )
                    return

                logger.info(
                    "Deposit rejected",
                    deposit_id=deposit_id,
                    user_id=deposit.user_id,
                    admin_id=callback.from_user.id,
                )

    # ── Update admin's message (remove buttons) ───────────────
    if action == DepositAction.APPROVE:
        status_text = "✅ <b>APPROVED</b>"
        new_balance_line = ""
        async with async_session_factory() as session:
            w_repo = WalletRepository(session)
            wallet = await w_repo.get_by_user_id(deposit.user_id)
            if wallet:
                new_balance_line = f"\n<b>New Balance:</b>  ${wallet.balance:.2f}"

        await callback.message.edit_text(
            f"{callback.message.text}\n\n"
            f"─────────────────────\n"
            f"{status_text} by <code>{callback.from_user.id}</code>"
            f"{new_balance_line}",
        )
    else:
        status_text = "❌ <b>REJECTED</b>"
        await callback.message.edit_text(
            f"{callback.message.text}\n\n"
            f"─────────────────────\n"
            f"{status_text} by <code>{callback.from_user.id}</code>",
        )

    await callback.answer(f"Deposit {action.value}d.", show_alert=False)

    # ── Notify the user ───────────────────────────────────────
    try:
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


# ═════════════════════════════════════════════════════════════════════════════
#  PROMO CODE MANAGEMENT  (/addpromo, /listpromos, /delpromo)
# ═════════════════════════════════════════════════════════════════════════════

@router.message(Command("addpromo"))
async def cmd_addpromo(message: Message, state: FSMContext) -> None:
    """Start the add-promo-code flow."""
    await state.set_state(AddPromoForm.waiting_for_code)
    await message.answer(
        "🎁 <b>Add Promo Code</b>\n\n"
        "<b>Step 1/4</b> — Enter the promo <b>code</b> string:\n\n"
        "<i>Example: WELCOME2024</i>\n\n"
        "Send /cancel to abort."
    )


@router.message(AddPromoForm.waiting_for_code, F.text)
async def process_promo_code_name(message: Message, state: FSMContext) -> None:
    code = message.text.strip().upper()
    if len(code) < 2 or len(code) > 128:
        await message.answer("⚠️ Code must be 2–128 characters.")
        return
    await state.update_data(promo_code=code)
    await state.set_state(AddPromoForm.waiting_for_description)
    await message.answer(
        f"✅ Code: <code>{code}</code>\n\n"
        "<b>Step 2/4</b> — Enter a <b>description</b> (admin note):\n\n"
        "<i>Example: Free trial for influencer campaign</i>\n\n"
        "Or send <code>-</code> to skip."
    )


@router.message(AddPromoForm.waiting_for_description, F.text)
async def process_promo_description(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    description = None if raw == "-" else raw
    await state.update_data(promo_description=description)
    await state.set_state(AddPromoForm.waiting_for_max_uses)
    await message.answer(
        "<b>Step 3/4</b> — How many times can this code be used?\n\n"
        "<i>Enter a number (e.g. 1 for single-use, 100 for 100 uses, 0 for unlimited)</i>"
    )


@router.message(AddPromoForm.waiting_for_max_uses, F.text)
async def process_promo_max_uses(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    try:
        max_uses = int(raw)
    except ValueError:
        await message.answer("⚠️ Please enter a number (e.g. 1, 10, 0 for unlimited).")
        return
    if max_uses < 0:
        await message.answer("⚠️ Must be 0 or greater.")
        return
    await state.update_data(promo_max_uses=max_uses)
    await state.set_state(AddPromoForm.waiting_for_product_id)
    await message.answer(
        f"✅ Max uses: <b>{'Unlimited' if max_uses == 0 else max_uses}</b>\n\n"
        "<b>Step 4/4</b> — Restrict to a specific product?\n\n"
        "Enter a product ID (use /products to see IDs), or send <code>-</code> "
        "to allow this code for <b>any</b> product."
    )


@router.message(AddPromoForm.waiting_for_product_id, F.text)
async def process_promo_product_id(
    message: Message,
    state: FSMContext,
    promo_repo: PromoCodeRepository,
) -> None:
    raw = message.text.strip()
    product_id = None
    if raw != "-":
        if not raw.isdigit():
            await message.answer("⚠️ Enter a numeric product ID or <code>-</code> for any product.")
            return
        product_id = int(raw)

    fsm_data = await state.get_data()
    await state.clear()

    promo = await promo_repo.create_code(
        code=fsm_data["promo_code"],
        description=fsm_data.get("promo_description"),
        product_id=product_id,
        max_uses=fsm_data["promo_max_uses"],
        requires_approval=True,
    )

    scope = f"Product #{product_id}" if product_id else "Any product"
    uses = "Unlimited" if promo.max_uses == 0 else str(promo.max_uses)

    await message.answer(
        f"✅ <b>Promo Code Created!</b>\n\n"
        f"<b>Code:</b>        <code>{promo.code}</code>\n"
        f"<b>Description:</b> {promo.description or '—'}\n"
        f"<b>Max Uses:</b>    {uses}\n"
        f"<b>Scope:</b>       {scope}\n"
        f"<b>Status:</b>      ✅ Active"
    )


@router.message(Command("listpromos"))
async def cmd_listpromos(
    message: Message,
    promo_repo: PromoCodeRepository,
) -> None:
    """List all promo codes."""
    codes = await promo_repo.list_codes()
    if not codes:
        await message.answer("🎁 No promo codes yet. Use /addpromo to create one.")
        return

    lines = ["🎁 <b>Promo Codes</b>\n"]
    for c in codes:
        status = "✅" if c.is_active else "🚫"
        uses_display = "∞" if c.max_uses == 0 else f"{c.used_count}/{c.max_uses}"
        scope = f"Product #{c.product_id}" if c.product_id else "Any"
        lines.append(
            f"{status} <code>{c.code}</code> (#{c.id})\n"
            f"   Used: {uses_display}  |  Scope: {scope}"
        )

    lines.append("\n<i>Use /delpromo &lt;id&gt; to deactivate a code.</i>")
    await message.answer("\n".join(lines))


@router.message(Command("delpromo"))
async def cmd_delpromo(
    message: Message,
    promo_repo: PromoCodeRepository,
) -> None:
    """
    Deactivate a promo code.
    Usage: /delpromo <code_id>
    """
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("⚠️ Usage: <code>/delpromo &lt;code_id&gt;</code>\nUse /listpromos to see IDs.")
        return
    code_id = int(args[1].strip())
    await promo_repo.deactivate_code(code_id)
    await message.answer(f"🚫 Promo code #{code_id} has been deactivated.")
