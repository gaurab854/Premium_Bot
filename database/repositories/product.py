"""
ProductRepository — all database queries for the ``products`` table.

Handles product listing, search by category, and stock count queries
that join with inventory.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.inventory import Inventory
from database.models.product import Product


class ProductRepository:
    """Encapsulates every database operation on the ``Product`` model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─────────────────────────────────────────────────────────
    #  CREATE
    # ─────────────────────────────────────────────────────────
    async def create(
        self,
        name: str,
        price: float,
        description: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Product:
        """Add a new product to the catalogue."""
        product = Product(
            name=name,
            price=price,
            description=description,
            category=category,
            is_available=True,
        )
        self._session.add(product)
        await self._session.flush()
        await self._session.refresh(product)
        return product

    # ─────────────────────────────────────────────────────────
    #  READ
    # ─────────────────────────────────────────────────────────
    async def get_by_id(self, product_id: int) -> Optional[Product]:
        """Fetch a product by PK."""
        return await self._session.get(Product, product_id)

    async def get_available(
        self,
        *,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> Sequence[Product]:
        """
        Return all available products, optionally filtered by category.
        """
        stmt = (
            select(Product)
            .where(Product.is_available.is_(True))
            .order_by(Product.name.asc())
            .limit(limit)
        )
        if category:
            stmt = stmt.where(Product.category == category)

        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_categories(self) -> Sequence[str]:
        """Return all distinct product categories."""
        stmt = (
            select(Product.category)
            .where(
                Product.is_available.is_(True),
                Product.category.isnot(None),
            )
            .distinct()
            .order_by(Product.category.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_stock_count(self, product_id: int) -> int:
        """Return the number of unsold inventory items for a product."""
        stmt = (
            select(func.count(Inventory.id))
            .where(
                Inventory.product_id == product_id,
                Inventory.is_sold.is_(False),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    # ─────────────────────────────────────────────────────────
    #  UPDATE
    # ─────────────────────────────────────────────────────────
    async def set_available(
        self,
        product_id: int,
        *,
        available: bool,
    ) -> None:
        """Show or hide a product from the catalogue."""
        stmt = (
            update(Product)
            .where(Product.id == product_id)
            .values(is_available=available)
        )
        await self._session.execute(stmt)

    async def update_price(
        self,
        product_id: int,
        new_price: float,
    ) -> None:
        """Update a product's price."""
        stmt = (
            update(Product)
            .where(Product.id == product_id)
            .values(price=new_price)
        )
        await self._session.execute(stmt)
