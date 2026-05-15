"""
Bot entrypoint — ``python -m bot``.

Supports TWO modes:

  1. **Polling** (default, development):
     Calls ``dp.start_polling()`` — the bot connects to Telegram and
     pulls updates.  No public IP or SSL required.

  2. **Webhook** (production, when ``WEBHOOK_ENABLED=true``):
     Registers a Telegram webhook and starts an aiohttp server on
     ``WEBHOOK_HOST:WEBHOOK_PORT``.  Nginx terminates SSL and proxies
     ``/webhook/<token>`` traffic to this server.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)
from aiohttp import web
from redis.asyncio import Redis
import structlog

from config import settings
from database import async_engine, Base

# ── Import routers ────────────────────────────────────────────
from bot.handlers import common, admin, user, deposit, purchase, checkout

# ── Import middleware ─────────────────────────────────────────
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.middlewares.channel import ChannelMemberMiddleware


# ══════════════════════════════════════════════════════════════
#  Lifecycle hooks
# ══════════════════════════════════════════════════════════════

async def on_startup(bot: Bot) -> None:
    """Run once when the bot starts (both modes)."""
    # Create tables if they don't exist (use Alembic in production)
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ── Set webhook if enabled ────────────────────────────────
    if settings.webhook.enabled:
        webhook_url = (
            f"{settings.webhook.url}/webhook/"
            f"{settings.bot.token.get_secret_value()}"
        )
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.webhook.secret_token,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
        structlog.get_logger().info(
            "Webhook registered",
            url=webhook_url.split("/webhook/")[0] + "/webhook/***",
        )

    bot_info = await bot.me()
    structlog.get_logger().info(
        "Bot started",
        username=bot_info.username,
        id=bot_info.id,
        mode="webhook" if settings.webhook.enabled else "polling",
    )

    # Notify admins
    for admin_id in settings.bot.admin_ids:
        try:
            mode = "🌐 Webhook" if settings.webhook.enabled else "🔄 Polling"
            await bot.send_message(
                admin_id,
                f"🟢 <b>Bot started successfully!</b>\n"
                f"Mode: {mode}",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Announce to channel that server is back online
    if settings.bot.channel_id:
        try:
            await bot.send_message(
                settings.bot.channel_id,
                "✅ <b>Server is back online!</b>\n\n"
                "🛡️ All services are operational. You can now browse and purchase products normally.",
                parse_mode="HTML",
            )
        except Exception:
            pass


async def on_shutdown(bot: Bot) -> None:
    """Cleanup on graceful shutdown."""
    structlog.get_logger().info("Bot shutting down …")

    # Announce to channel that server is under maintenance
    if settings.bot.channel_id:
        try:
            await bot.send_message(
                settings.bot.channel_id,
                "🔧 <b>Server is under maintenance.</b>\n\n"
                "⏳ The bot will be temporarily unavailable. We'll be back shortly!",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Remove webhook on shutdown
    if settings.webhook.enabled:
        await bot.delete_webhook(drop_pending_updates=True)
        structlog.get_logger().info("Webhook removed")

    await async_engine.dispose()

    # Notify admins
    for admin_id in settings.bot.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                "🔴 <b>Bot is shutting down.</b>",
            )
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
#  Build the Dispatcher (shared by both modes)
# ══════════════════════════════════════════════════════════════

def _build_dispatcher(storage: RedisStorage) -> Dispatcher:
    """Create and configure the Dispatcher with all routers & middleware."""
    dp = Dispatcher(storage=storage)

    # ── Register lifecycle hooks ──────────────────────────────
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # ── Register middleware (outer → inner) ───────────────────
    # Channel gate runs FIRST (outermost) so non-members can't bypass it
    dp.message.middleware(ChannelMemberMiddleware())
    dp.callback_query.middleware(ChannelMemberMiddleware())
    dp.message.middleware(DatabaseMiddleware())
    dp.callback_query.middleware(DatabaseMiddleware())
    dp.message.middleware(ThrottlingMiddleware(rate_limit=settings.rate_limit))

    # ── Include routers ───────────────────────────────────────
    dp.include_routers(
        admin.router,      # admin-only commands + deposit callbacks
        checkout.router,   # checkout gateway (Pay / Promo Code) + promo approval
        deposit.router,    # user deposit FSM flow
        purchase.router,   # product browsing + order history
        user.router,
        common.router,     # catch-all last
    )

    return dp


# ══════════════════════════════════════════════════════════════
#  Polling mode (development)
# ══════════════════════════════════════════════════════════════

async def _run_polling(bot: Bot, dp: Dispatcher, redis: Redis) -> None:
    """Start the bot in long-polling mode (no public IP needed)."""
    structlog.get_logger().info(
        "Starting in POLLING mode",
    )
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        await redis.aclose()


# ══════════════════════════════════════════════════════════════
#  Webhook mode (production)
# ══════════════════════════════════════════════════════════════

async def _run_webhook(bot: Bot, dp: Dispatcher, redis: Redis) -> None:
    """
    Start the bot as an aiohttp web server receiving webhook POSTs.

    Nginx proxies HTTPS :443 → this server on :8443 (internal).
    """
    structlog.get_logger().info(
        "Starting in WEBHOOK mode",
        host=settings.webhook.host,
        port=settings.webhook.port,
    )

    # Build the webhook path: /webhook/<bot_token>
    webhook_path = f"/webhook/{settings.bot.token.get_secret_value()}"

    # Create the aiohttp app
    app = web.Application()

    # Register the webhook handler
    handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook.secret_token,
    )
    handler.register(app, path=webhook_path)

    # Wire up Aiogram's startup/shutdown into aiohttp's lifecycle
    setup_application(app, dp, bot=bot)

    # ── Health check endpoint ─────────────────────────────────
    async def health_handler(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "mode": "webhook"})

    app.router.add_get("/health", health_handler)

    # ── Start the server ──────────────────────────────────────
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host=settings.webhook.host,
        port=settings.webhook.port,
    )
    await site.start()

    structlog.get_logger().info(
        "Webhook server running",
        path=f"/webhook/***",
        listen=f"{settings.webhook.host}:{settings.webhook.port}",
    )

    # Keep the server alive until interrupted
    try:
        await asyncio.Event().wait()  # block forever
    finally:
        await runner.cleanup()
        await bot.session.close()
        await redis.aclose()


# ══════════════════════════════════════════════════════════════
#  Main entry point
# ══════════════════════════════════════════════════════════════

async def main() -> None:
    """Bootstrap and run the bot in the configured mode."""
    # ── Logging ───────────────────────────────────────────────
    logging.basicConfig(
        level=settings.logging_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.logging_level),
        ),
    )

    # ── Redis (FSM storage + throttling) ──────────────────────
    import os
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        redis = Redis.from_url(redis_url, decode_responses=True)
    else:
        redis = Redis(
            host=settings.redis.host,
            port=settings.redis.port,
            db=settings.redis.db,
            password=(
                settings.redis.password.get_secret_value()
                if settings.redis.password
                else None
            ),
            decode_responses=True,
        )
    storage = RedisStorage(redis=redis)

    # ── Bot & Dispatcher ──────────────────────────────────────
    bot = Bot(
        token=settings.bot.token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = _build_dispatcher(storage)

    # ── Choose mode ───────────────────────────────────────────
    if settings.webhook.enabled:
        await _run_webhook(bot, dp, redis)
    else:
        await _run_polling(bot, dp, redis)


if __name__ == "__main__":
    asyncio.run(main())
