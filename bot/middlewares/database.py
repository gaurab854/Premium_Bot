"""
Database middleware — injects an async session and all repository
instances into every handler automatically.

Handlers declare the repos they need in their signature::

    async def cmd_start(message: Message, user_repo: UserRepository) -> None: ...
    async def cmd_buy(callback: CallbackQuery, wallet_repo: WalletRepository,
                      inventory_repo: InventoryRepository) -> None: ...
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from database import async_session_factory
from database.repositories.user import UserRepository
from database.repositories.wallet import WalletRepository
from database.repositories.inventory import InventoryRepository
from database.repositories.deposit import DepositRepository
from database.repositories.product import ProductRepository
from database.repositories.order import OrderRepository
from database.repositories.promo_code import PromoCodeRepository


class DatabaseMiddleware(BaseMiddleware):
    """
    Opens a single ``AsyncSession`` per incoming update, creates
    all repository instances on that session, and injects them into
    the handler's ``data`` dict.

    Injected keys:
        • ``session``        → the raw ``AsyncSession``
        • ``user_repo``      → ``UserRepository``
        • ``wallet_repo``    → ``WalletRepository``
        • ``inventory_repo`` → ``InventoryRepository``
        • ``deposit_repo``   → ``DepositRepository``
        • ``product_repo``   → ``ProductRepository``
        • ``order_repo``     → ``OrderRepository``

    The session is auto-committed on success and rolled back on error.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with async_session_factory() as session:
            # Inject the raw session and all repositories
            data["session"] = session
            data["user_repo"] = UserRepository(session)
            data["wallet_repo"] = WalletRepository(session)
            data["inventory_repo"] = InventoryRepository(session)
            data["deposit_repo"] = DepositRepository(session)
            data["product_repo"] = ProductRepository(session)
            data["order_repo"] = OrderRepository(session)
            data["promo_repo"] = PromoCodeRepository(session)

            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
