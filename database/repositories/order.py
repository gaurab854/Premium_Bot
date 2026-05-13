"""
OrderRepository — all database queries for ``orders`` + ``order_items``.

An Order is the parent record; OrderItem is the line-item detail linking
back to a product and the specific inventory item that was fulfilled.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.order import Order, OrderStatus
from database.models.order_item import OrderItem


class OrderRepository:
    """Encapsulates every database operation on Orders and OrderItems."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─────────────────────────────────────────────────────────
    #  CREATE
    # ─────────────────────────────────────────────────────────
    async def create_order(
        self,
        user_id: int,
        total_amount: float,
        *,
        status: OrderStatus = OrderStatus.COMPLETED,
    ) -> Order:
        """
        Create a new order header.

        For instant digital-goods delivery the order is created
        directly as COMPLETED.  Use PENDING for manual fulfilment.
        """
        order = Order(
            user_id=user_id,
            total_amount=total_amount,
            status=status,
        )
        self._session.add(order)
        await self._session.flush()
        await self._session.refresh(order)
        return order

    async def create_order_item(
        self,
        order_id: int,
        product_id: int,
        price_at_purchase: float,
        *,
        inventory_id: Optional[int] = None,
        quantity: int = 1,
    ) -> OrderItem:
        """
        Attach a line item to an existing order.

        ``price_at_purchase`` is a snapshot so retroactive price
        changes never alter historical order data.
        """
        item = OrderItem(
            order_id=order_id,
            product_id=product_id,
            inventory_id=inventory_id,
            quantity=quantity,
            price_at_purchase=price_at_purchase,
        )
        self._session.add(item)
        await self._session.flush()
        await self._session.refresh(item)
        return item

    async def create_full_order(
        self,
        user_id: int,
        product_id: int,
        inventory_id: int,
        price: float,
        *,
        quantity: int = 1,
    ) -> tuple[Order, OrderItem]:
        """
        Convenience: create an Order + its single OrderItem in one call.

        Returns:
            A ``(Order, OrderItem)`` tuple.
        """
        order = await self.create_order(
            user_id=user_id,
            total_amount=price * quantity,
            status=OrderStatus.COMPLETED,
        )
        item = await self.create_order_item(
            order_id=order.id,
            product_id=product_id,
            inventory_id=inventory_id,
            price_at_purchase=price,
            quantity=quantity,
        )
        return order, item

    # ─────────────────────────────────────────────────────────
    #  READ
    # ─────────────────────────────────────────────────────────
    async def get_by_id(self, order_id: int) -> Optional[Order]:
        """Fetch an order by PK (eager-loads items)."""
        stmt = select(Order).where(Order.id == order_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user(
        self,
        user_id: int,
        *,
        limit: int = 20,
    ) -> Sequence[Order]:
        """Return a user's order history (newest first)."""
        stmt = (
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_by_user(self, user_id: int) -> int:
        """Return total number of orders for a user."""
        stmt = (
            select(func.count(Order.id))
            .where(Order.user_id == user_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    # ─────────────────────────────────────────────────────────
    #  UPDATE
    # ─────────────────────────────────────────────────────────
    async def set_status(
        self,
        order_id: int,
        new_status: OrderStatus,
    ) -> None:
        """Update an order's status."""
        stmt = (
            update(Order)
            .where(Order.id == order_id)
            .values(status=new_status)
        )
        await self._session.execute(stmt)
