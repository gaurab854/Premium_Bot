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
        allow_promo: bool = False,
    ) -> Product:
        """Add a new product to the catalogue."""
        product = Product(
            name=name,
            price=price,
            description=description,
            category=category,
            is_available=True,
            allow_promo=allow_promo,
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

    async def get_all_with_counts(self, limit: int = 50) -> Sequence[tuple[Product, int, int]]:
        """
        Return products with their available and sold stock counts in a single query.
        Returns a list of (Product, available_count, sold_count).
        """
        # Subquery for available stock
        available_sub = (
            select(Inventory.product_id, func.count(Inventory.id).label("count"))
            .where(Inventory.is_sold.is_(False))
            .group_by(Inventory.product_id)
            .subquery()
        )
        # Subquery for sold stock
        sold_sub = (
            select(Inventory.product_id, func.count(Inventory.id).label("count"))
            .where(Inventory.is_sold.is_(True))
            .group_by(Inventory.product_id)
            .subquery()
        )

        stmt = (
            select(
                Product,
                func.coalesce(available_sub.c.count, 0),
                func.coalesce(sold_sub.c.count, 0),
            )
            .outerjoin(available_sub, Product.id == available_sub.c.product_id)
            .outerjoin(sold_sub, Product.id == sold_sub.c.product_id)
            .order_by(Product.id.desc())
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        return result.all()

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

    async def set_allow_promo(
        self,
        product_id: int,
        *,
        allow_promo: bool,
    ) -> None:
        """Enable or disable promo codes for a product."""
        stmt = (
            update(Product)
            .where(Product.id == product_id)
            .values(allow_promo=allow_promo)
        )
        await self._session.execute(stmt)

    # ─────────────────────────────────────────────────────────
    #  DELETE
    # ─────────────────────────────────────────────────────────
    async def hard_delete(self, product_id: int) -> bool:
        """
        Permanently delete a product and all its unsold inventory from the DB.

        Sold inventory items are kept for order history integrity.
        After deletion the PostgreSQL sequence is reset to the lowest
        available gap so new products reuse freed IDs.

        Returns True if the product existed and was deleted.
        Raises sqlalchemy.exc.IntegrityError if the product cannot be deleted
        due to existing orders (RESTRICT constraint).
        """
        from sqlalchemy import delete, text
        from database.models.inventory import Inventory
        from database.models.order_item import OrderItem

        product = await self._session.get(Product, product_id)
        if product is None:
            return False

        # 1. Delete all order items associated with this product
        await self._session.execute(
            delete(OrderItem).where(
                OrderItem.product_id == product_id
            )
        )

        # 2. Delete all inventory associated with this product (both sold and unsold)
        await self._session.execute(
            delete(Inventory).where(
                Inventory.product_id == product_id
            )
        )

        # 3. Hard-delete the product row
        await self._session.delete(product)
        await self._session.flush()

        # 4. Reset sequence to fill ID gaps — next product reuses the lowest free ID
        await self._session.execute(
            text(
                """
                SELECT setval(
                    pg_get_serial_sequence('products', 'id'),
                    COALESCE((SELECT MAX(id) FROM products), 1),
                    (SELECT MAX(id) FROM products) IS NOT NULL
                )
                """
            )
        )

        return True
