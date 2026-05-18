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
    /announce     — Send a custom message to the channel as the bot

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
from bot.states.user import AddProductForm, AddStockForm, AnnounceForm
from config import settings
from database import async_session_factory
from database.repositories.deposit import DepositRepository
from database.repositories.inventory import InventoryRepository
from database.repositories.product import ProductRepository
from database.repositories.user import UserRepository
from database.repositories.wallet import WalletRepository

logger = structlog.get_logger()

router = Router(name="admin")
router.message.filter(AdminFilter())


async def broadcast_announcement(bot: Bot, user_repo: UserRepository, text: str) -> int:
    """Broadcast a text message to all active users in chunks."""
    offset = 0
    limit = 100
    sent_count = 0
    while True:
        users = await user_repo.get_all_active(limit=limit, offset=offset)
        if not users:
            break
        for u in users:
            try:
                await bot.send_message(u.telegram_id, text)
                sent_count += 1
            except Exception:
                pass
        offset += limit
    return sent_count


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


@router.message(AddProductForm.waiting_for_name, Command("cancel"))
async def cancel_addproduct(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Cancelled. Product was not added.")


@router.message(AddProductForm.waiting_for_name, F.text & ~F.text.startswith("/"))
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


@router.message(AddProductForm.waiting_for_price, F.text & ~F.text.startswith("/"))
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
        "<b>Step 3/5</b> — Choose the <b>category</b>:\n\n"
        "<code>a</code> — Link / ID Pass\n"
        "<code>b</code> — Gmail Invite\n"
        "<code>c</code> — Full Warranty ID Pass"
    )


@router.message(AddProductForm.waiting_for_category, F.text & ~F.text.startswith("/"))
async def process_product_category(message: Message, state: FSMContext) -> None:
    CATEGORY_MAP = {
        "a": "LINK / ID PASS",
        "b": "GMAIL INVITE",
        "c": "FULL WARRANTY",
    }
    choice = message.text.strip().lower()
    category = CATEGORY_MAP.get(choice)
    if not category:
        await message.answer(
            "⚠️ Please reply with:\n"
            "<code>a</code> — Link / ID Pass\n"
            "<code>b</code> — Gmail Invite\n"
            "<code>c</code> — Full Warranty ID Pass"
        )
        return

    await state.update_data(product_category=category)
    await state.set_state(AddProductForm.waiting_for_description)
    await message.answer(
        f"✅ Category: <b>{category}</b>\n\n"
        "<b>Step 4/5</b> — Enter the <b>description</b>:\n\n"
        "<i>What does the user get? e.g. '1-month Netflix Premium with 4 screens'</i>\n\n"
        "Or send <code>-</code> to skip."
    )


@router.message(AddProductForm.waiting_for_description, F.text & ~F.text.startswith("/"))
async def process_product_description(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    description = None if raw == "-" else raw

    fsm_data = await state.get_data()
    category = fsm_data.get("product_category")

    if category == "GMAIL INVITE":
        await state.set_state(AddProductForm.waiting_for_stock_count)
        await message.answer(
            f"✅ Description saved.\n\n"
            "<b>Step 5/5</b> — How many Gmail invitation slots are available?\n\n"
            "<i>Enter a number (e.g. 50).</i>"
        )
    elif category == "FULL WARRANTY":
        await state.set_state(AddProductForm.waiting_for_codes)
        await message.answer(
            f"✅ Description saved.\n\n"
            "<b>Step 5/5</b> — Enter the <b>inventory codes</b> for this Full Warranty product.\n\n"
            "📋 Send <b>one code per line</b> (e.g. <code>user:pass</code>):\n\n"
            "<i>Each line = one unit of stock. You can add more later with /addstock.</i>"
        )
    else:  # LINK / ID PASS
        await state.set_state(AddProductForm.waiting_for_codes)
        await message.answer(
            f"✅ Description saved.\n\n"
            "<b>Step 5/5</b> — Now enter the <b>inventory codes</b> (links or id:pass).\n\n"
            "📋 Send <b>one code per line</b>:\n"
            "<code>user:pass\nuser2:pass2</code>\n\n"
            "<i>Each line = one unit of stock. You can add more later with /addstock.</i>"
        )


@router.message(AddProductForm.waiting_for_codes, F.text & ~F.text.startswith("/"))
async def process_product_codes(
    message: Message,
    state: FSMContext,
    bot: Bot,
    product_repo: ProductRepository,
    inventory_repo: InventoryRepository,
    user_repo: UserRepository,
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

    # ── Announce to channel & users ───────────────────────────
    desc_text = f"\n📄 {product.description}\n" if product.description else ""
    cat_text = f"🏷️ <b>Category:</b> {product.category}\n" if product.category else ""
    announcement_text = (
        f"🆕 <b>New Product Available!</b>\n\n"
        f"🛍️ <b>{product.name}</b>\n"
        f"{desc_text}"
        f"\n{cat_text}"
        f"💰 <b>Price:</b> ${product.price:.2f}\n"
        f"📦 <b>In Stock:</b> {count} unit(s)\n\n"
        f"👉 Use /shop in the bot to purchase!"
    )

    if settings.bot.channel_id:
        try:
            await bot.send_message(settings.bot.channel_id, announcement_text)
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

    await message.answer("📢 Broadcasting announcement to all users. This might take a moment...")
    sent = await broadcast_announcement(bot, user_repo, announcement_text)
    await message.answer(f"✅ Announcement sent to {sent} user(s)!")


@router.message(AddProductForm.waiting_for_stock_count, F.text & ~F.text.startswith("/"))
async def process_product_stock_count(
    message: Message,
    state: FSMContext,
    bot: Bot,
    product_repo: ProductRepository,
    inventory_repo: InventoryRepository,
    user_repo: UserRepository,
) -> None:
    """Save the GMAIL product with a specified number of placeholder stock items."""
    raw = message.text.strip()
    if not raw.isdigit():
        await message.answer("⚠️ Please enter a valid number (e.g. 50).")
        return
    
    count = int(raw)
    if count <= 0:
        await message.answer("⚠️ Stock count must be greater than 0.")
        return

    codes = ["[GMAIL INVITE]" for _ in range(count)]

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
    await inventory_repo.add_bulk(product.id, codes)

    logger.info(
        "Product created (GMAIL)",
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
        f"<b>Stock added:</b> {count} invitation(s)\n\n"
        f"📢 Sending announcement to the channel..."
    )

    # ── Announce to channel & users ───────────────────────────
    desc_text = f"\n📄 {product.description}\n" if product.description else ""
    cat_text = f"🏷️ <b>Category:</b> {product.category}\n" if product.category else ""
    announcement_text = (
        f"🆕 <b>New Product Available!</b>\n\n"
        f"🛍️ <b>{product.name}</b>\n"
        f"{desc_text}"
        f"\n{cat_text}"
        f"💰 <b>Price:</b> ${product.price:.2f}\n"
        f"📦 <b>In Stock:</b> {count} unit(s)\n\n"
        f"👉 Use /shop in the bot to purchase!"
    )

    if settings.bot.channel_id:
        try:
            await bot.send_message(settings.bot.channel_id, announcement_text)
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

    await message.answer("📢 Broadcasting announcement to all users. This might take a moment...")
    sent = await broadcast_announcement(bot, user_repo, announcement_text)
    await message.answer(f"✅ Announcement sent to {sent} user(s)!")


# ═════════════════════════════════════════════════════════════════════════════
#  /products — list all products (admin view with stock)
# ═════════════════════════════════════════════════════════════════════════════

@router.message(Command("products"))
async def cmd_products(
    message: Message,
    product_repo: ProductRepository,
) -> None:
    """List all products with their current stock counts."""
    # Use optimized single query to get products + counts
    results = await product_repo.get_all_with_counts(limit=50)

    if not results:
        await message.answer("🏪 No products in the catalogue yet.\nUse /addproduct to add one.")
        return

    lines = ["📦 <b>Product Catalogue (Admin View)</b>\n"]
    for product, stock, sold in results:
        status = "✅" if product.is_available else "🚫"
        lines.append(
            f"{status} <b>#{product.id} — {product.name}</b>\n"
            f"   💰 ${product.price:.2f}  |  📦 {stock} in stock  |  ✅ {sold} sold\n"
            f"   🏷️ {product.category or 'No category'}\n"
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
    state: FSMContext,
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

    await state.update_data(addstock_product_id=product_id)

    if product.category and "GMAIL" in product.category.upper():
        await state.set_state(AddStockForm.waiting_for_stock_count)
        await message.answer(
            f"📦 <b>Adding stock to: {product.name}</b>\n"
            f"Current stock: {stock} unit(s)\n\n"
            f"How many stocks (invitations) are you adding?\n"
            f"<i>Enter a number (e.g. 50).</i>\n\n"
            f"Send /cancel to abort."
        )
    else:
        await state.set_state(AddStockForm.waiting_for_codes)
        await message.answer(
            f"📦 <b>Adding stock to: {product.name}</b>\n"
            f"Current stock: {stock} unit(s)\n\n"
            f"Now send the codes, <b>one per line</b>:\n"
            f"<code>CODE1\nCODE2\nCODE3</code>\n\n"
            f"Send /cancel to abort."
        )


@router.message(AddStockForm.waiting_for_codes, Command("cancel"))
@router.message(AddStockForm.waiting_for_stock_count, Command("cancel"))
async def cancel_addstock(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Cancelled. Stock was not added.")


@router.message(AddStockForm.waiting_for_codes, F.text & ~F.text.startswith("/"))
async def process_addstock_codes(
    message: Message,
    state: FSMContext,
    bot: Bot,
    inventory_repo: InventoryRepository,
    product_repo: ProductRepository,
    user_repo: UserRepository,
) -> None:
    """Receive codes for /addstock and bulk-insert into inventory."""
    raw = message.text.strip()
    codes = [line.strip() for line in raw.splitlines() if line.strip()]

    if not codes:
        await message.answer("⚠️ No codes found. Send at least one code (one per line).")
        return

    fsm_data = await state.get_data()
    product_id = fsm_data.get("addstock_product_id")
    await state.clear()

    product = await product_repo.get_by_id(product_id)
    if not product:
        await message.answer(f"⚠️ Product #{product_id} no longer exists.")
        return

    count = await inventory_repo.add_bulk(product_id, codes)
    new_stock = await inventory_repo.count_available(product_id)

    logger.info(
        "Stock added",
        product_id=product_id,
        codes_added=count,
        new_total=new_stock,
    )

    await message.answer(
        f"✅ <b>Stock Updated!</b>\n\n"
        f"<b>Product:</b>    {product.name}\n"
        f"<b>Added:</b>      {count} code(s)\n"
        f"<b>Total stock:</b> {new_stock} unit(s)\n\n"
        f"📢 Sending announcement to the channel..."
    )

    # ── Announce stock restock to channel & users ────────────
    announcement_text = (
        f"🔄 <b>Stock Restocked!</b>\n\n"
        f"🛍️ <b>{product.name}</b>\n"
        f"📦 <b>Available now:</b> {new_stock} unit(s)\n\n"
        f"👉 Use /shop in the bot to purchase!"
    )

    if settings.bot.channel_id:
        try:
            await bot.send_message(settings.bot.channel_id, announcement_text)
            await message.answer("✅ Channel announcement sent!")
        except Exception as e:
            logger.warning("Failed to send stock announcement", error=str(e))
            await message.answer(
                f"⚠️ Stock added but channel announcement failed: {e}\n"
                "Make sure the bot is an admin in your channel."
            )
    else:
        await message.answer(
            "ℹ️ No channel configured. Set <code>BOT_CHANNEL_ID</code> in your .env "
            "to enable channel announcements."
        )

    await message.answer("📢 Broadcasting restock announcement to all users. This might take a moment...")
    sent = await broadcast_announcement(bot, user_repo, announcement_text)
    await message.answer(f"✅ Restock announcement sent to {sent} user(s)!")


@router.message(AddStockForm.waiting_for_stock_count, F.text & ~F.text.startswith("/"))
async def process_addstock_count(
    message: Message,
    state: FSMContext,
    bot: Bot,
    inventory_repo: InventoryRepository,
    product_repo: ProductRepository,
    user_repo: UserRepository,
) -> None:
    """Receive number of GMAIL invitations and bulk-insert placeholders."""
    raw = message.text.strip()
    if not raw.isdigit():
        await message.answer("⚠️ Please enter a valid number (e.g. 50).")
        return
    
    count = int(raw)
    if count <= 0:
        await message.answer("⚠️ Stock count must be greater than 0.")
        return

    codes = ["[GMAIL INVITE]" for _ in range(count)]

    fsm_data = await state.get_data()
    product_id = fsm_data.get("addstock_product_id")
    await state.clear()

    product = await product_repo.get_by_id(product_id)
    if not product:
        await message.answer(f"⚠️ Product #{product_id} no longer exists.")
        return

    await inventory_repo.add_bulk(product_id, codes)
    new_stock = await inventory_repo.count_available(product_id)

    logger.info(
        "Stock added (GMAIL)",
        product_id=product_id,
        codes_added=count,
        new_total=new_stock,
    )

    await message.answer(
        f"✅ <b>Stock Updated!</b>\n\n"
        f"<b>Product:</b>    {product.name}\n"
        f"<b>Added:</b>      {count} invitation(s)\n"
        f"<b>Total stock:</b> {new_stock} unit(s)\n\n"
        f"📢 Sending announcement to the channel..."
    )

    # ── Announce stock restock to channel & users ────────────
    announcement_text = (
        f"🔄 <b>Stock Restocked!</b>\n\n"
        f"🛍️ <b>{product.name}</b>\n"
        f"📦 <b>Available now:</b> {new_stock} unit(s)\n\n"
        f"👉 Use /shop in the bot to purchase!"
    )

    if settings.bot.channel_id:
        try:
            await bot.send_message(settings.bot.channel_id, announcement_text)
            await message.answer("✅ Channel announcement sent!")
        except Exception as e:
            logger.warning("Failed to send stock announcement", error=str(e))
            await message.answer(
                f"⚠️ Stock added but channel announcement failed: {e}\n"
                "Make sure the bot is an admin in your channel."
            )
    else:
        await message.answer(
            "ℹ️ No channel configured. Set <code>BOT_CHANNEL_ID</code> in your .env "
            "to enable channel announcements."
        )

    await message.answer("📢 Broadcasting restock announcement to all users. This might take a moment...")
    sent = await broadcast_announcement(bot, user_repo, announcement_text)
    await message.answer(f"✅ Restock announcement sent to {sent} user(s)!")



@router.message(Command("delproduct"))
async def cmd_delproduct(
    message: Message,
    product_repo: ProductRepository,
) -> None:
    """
    Permanently delete a product from the database.
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

    product_name = product.name

    try:
        deleted = await product_repo.hard_delete(product_id)
        if deleted:
            await message.answer(
                f"🗑️ <b>Product Permanently Deleted</b>\n\n"
                f"<b>ID:</b>    #{product_id}\n"
                f"<b>Name:</b>  {product_name}\n\n"
                f"✅ All unsold inventory codes removed.\n"
                f"✅ Sales history (order items) for this product cleared.\n"
                f"♻️ ID #{product_id} will be reused for the next product."
            )
        else:
            await message.answer(f"⚠️ Failed to delete product #{product_id}.")
    except Exception as e:
        logger.error("Error deleting product", product_id=product_id, error=str(e))
        await message.answer(f"⚠️ An error occurred while deleting product #{product_id}.")


@router.message(Command("hideproduct"))
async def cmd_hideproduct(message: Message, product_repo: ProductRepository) -> None:
    """Hide a product from the catalogue."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("⚠️ Usage: <code>/hideproduct &lt;product_id&gt;</code>")
        return

    product_id = int(args[1].strip())
    product = await product_repo.get_by_id(product_id)
    if not product:
        await message.answer(f"⚠️ Product #{product_id} not found.")
        return

    await product_repo.set_available(product_id, available=False)
    await message.answer(f"🚫 Product <b>{product.name}</b> (#<code>{product_id}</code>) is now <b>HIDDEN</b> from the catalogue.")


@router.message(Command("showproduct"))
async def cmd_showproduct(message: Message, product_repo: ProductRepository) -> None:
    """Show a product in the catalogue."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("⚠️ Usage: <code>/showproduct &lt;product_id&gt;</code>")
        return

    product_id = int(args[1].strip())
    product = await product_repo.get_by_id(product_id)
    if not product:
        await message.answer(f"⚠️ Product #{product_id} not found.")
        return

    await product_repo.set_available(product_id, available=True)
    await message.answer(f"✅ Product <b>{product.name}</b> (#<code>{product_id}</code>) is now <b>VISIBLE</b> in the catalogue.")


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
#  /announce — Send a custom message to the channel as the bot
# ═════════════════════════════════════════════════════════════════════════════

@router.message(Command("announce"))
async def cmd_announce(message: Message, state: FSMContext, bot: Bot) -> None:
    """
    Usage:
        /announce Your message here   ← inline (single line)

    OR just:
        /announce
    Then type the announcement text in the next message (supports multi-line).
    """
    # ── Inline usage: text after the command on the same line ──
    parts = message.text.split(maxsplit=1)
    if len(parts) >= 2:
        announcement_text = parts[1].strip()
        if announcement_text:
            await _send_channel_announcement(message, bot, announcement_text)
            return

    # ── Multi-step usage: ask for text in next message ─────────
    await state.set_state(AnnounceForm.waiting_for_text)
    await message.answer(
        "📢 <b>Channel Announcement</b>\n\n"
        "Type the announcement message below and send it.\n"
        "It will be posted to the channel as-is (HTML formatting supported).\n\n"
        "<i>Send /cancel to abort.</i>"
    )


@router.message(AnnounceForm.waiting_for_text, Command("cancel"))
async def cancel_announce(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Announcement cancelled.")


@router.message(AnnounceForm.waiting_for_text, F.text & ~F.text.startswith("/"))
async def process_announce_text(message: Message, state: FSMContext, bot: Bot) -> None:
    """Receive the announcement text from admin and post it to the channel."""
    announcement_text = message.text.strip()
    await state.clear()
    await _send_channel_announcement(message, bot, announcement_text)


async def _send_channel_announcement(message: Message, bot: Bot, text: str) -> None:
    """Post *text* to the configured channel and confirm to the admin."""
    if not settings.bot.channel_id:
        await message.answer(
            "⚠️ No channel configured.\n"
            "Set <code>BOT_CHANNEL_ID</code> in your .env file."
        )
        return

    try:
        sent = await bot.send_message(
            settings.bot.channel_id,
            text,
            parse_mode="HTML",
        )
        await message.answer(
            f"✅ <b>Announcement sent to channel!</b>\n\n"
            f"📌 Message ID: <code>{sent.message_id}</code>\n"
            f"📢 Channel: <code>{settings.bot.channel_id}</code>\n\n"
            f"<i>Preview of what was sent:</i>\n"
            f"─────────────────────\n"
            f"{text[:500]}{'…' if len(text) > 500 else ''}"
        )
        logger.info(
            "Channel announcement sent",
            admin_id=message.from_user.id,
            channel_id=settings.bot.channel_id,
            message_id=sent.message_id,
        )
    except Exception as e:
        logger.warning("Failed to send channel announcement", error=str(e))
        await message.answer(
            f"❌ <b>Failed to send announcement!</b>\n\n"
            f"<b>Error:</b> <code>{e}</code>\n\n"
            f"Make sure the bot is an <b>admin</b> in the channel."
        )


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
#  /fw — List all Full Warranty purchases
# ═════════════════════════════════════════════════════════════════════════════

@router.message(Command("fw"))
async def cmd_fw(message: Message) -> None:
    """Show all Full Warranty purchases in a simple tabular format."""
    from sqlalchemy import select
    from database.models.order_item import OrderItem
    from database.models.order import Order
    from database.models.product import Product
    from database.models.inventory import Inventory
    from database.models.user import User

    async with async_session_factory() as session:
        stmt = (
            select(
                Product.name,
                Inventory.data,
                User.telegram_id,
                User.username,
            )
            .join(OrderItem, OrderItem.inventory_id == Inventory.id)
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .join(User, User.id == Order.user_id)
            .where(
                Inventory.is_sold.is_(True),
                Product.category.ilike("FULL%WARRANTY"),
            )
            .order_by(Order.created_at.desc())
        )
        result = await session.execute(stmt)
        rows = result.all()

    if not rows:
        await message.answer(
            "🛡️ <b>Full Warranty Purchases</b>\n\n"
            "No full warranty purchases found yet."
        )
        return

    # Build output — chunk into 20 rows to stay under Telegram's message limit
    CHUNK = 20
    total = len(rows)
    for start in range(0, total, CHUNK):
        chunk = rows[start : start + CHUNK]
        lines = [
            f"🛡️ <b>Full Warranty Purchases</b> "
            f"({start + 1}–{min(start + CHUNK, total)} of {total})\n",
        ]
        for i, (prod_name, code, tg_id, username) in enumerate(chunk, start=start + 1):
            prod_short = (prod_name or "Unknown")[:28]
            buyer      = f"@{username}" if username else f"UID:{tg_id}"
            lines.append(
                f"<b>{i}. {prod_short}</b>\n"
                f"   👤 {buyer}\n"
                f"   🔑 <tg-spoiler>{code}</tg-spoiler>"
            )
        await message.answer("\n\n".join(lines))
