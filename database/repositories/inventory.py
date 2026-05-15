"""
InventoryRepository — all database queries for the ``inventory`` table.

Design notes:

  ┌───────────────────────────────────────────────────────────────┐
  │  PREVENTING OVERSELLING WITH  SELECT … FOR UPDATE SKIP LOCKED│
  ├───────────────────────────────────────────────────────────────┤
  │                                                               │
  │  The digital-goods problem:                                   │
  │    Two users click "Buy" at the exact same millisecond.       │
  │    A naïve SELECT + UPDATE would give BOTH users the same     │
  │    inventory row → one code is sold twice (overselling).      │
  │                                                               │
  │  How SELECT … FOR UPDATE SKIP LOCKED solves this:             │
  │                                                               │
  │    1. TX-A runs:                                              │
  │       SELECT * FROM inventory                                 │
  │         WHERE product_id = 5 AND is_sold = false              │
  │         LIMIT 1                                               │
  │         FOR UPDATE SKIP LOCKED;                               │
  │       → acquires a ROW-LEVEL LOCK on row #42                  │
  │                                                               │
  │    2. TX-B runs the same query at the same instant:           │
  │       → sees row #42 is locked → SKIPS it (does NOT wait)     │
  │       → locks the NEXT available row #43 instead              │
  │                                                               │
  │    3. TX-A marks row #42 as sold → commits → releases lock    │
  │    4. TX-B marks row #43 as sold → commits → releases lock    │
  │                                                               │
  │  Result: each buyer gets a DIFFERENT code. Zero overselling.  │
  │                                                               │
  │  Why FOR UPDATE alone is not enough:                          │
  │    Without SKIP LOCKED, TX-B would BLOCK and wait for TX-A    │
  │    to finish. That means sequential throughput — users wait    │
  │    in line even when 1 000 codes are in stock.                │
  │    SKIP LOCKED gives us both correctness AND concurrency.     │
  │                                                               │
  └───────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.inventory import Inventory


class InventoryRepository:
    """Encapsulates every database operation on the ``Inventory`` model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─────────────────────────────────────────────────────────
    #  CREATE
    # ─────────────────────────────────────────────────────────
    async def add_item(self, product_id: int, data: str) -> Inventory:
        """
        Add a single inventory item (hidden code) for a product.

        Args:
            product_id: FK to the products table.
            data:       The secret code / key / credentials.

        Returns:
            The newly created ``Inventory`` row.
        """
        item = Inventory(product_id=product_id, data=data, is_sold=False)
        self._session.add(item)
        await self._session.flush()
        await self._session.refresh(item)
        return item

    async def add_bulk(
        self,
        product_id: int,
        codes: Sequence[str],
    ) -> int:
        """
        Bulk-insert multiple inventory codes for a product.

        Args:
            product_id: FK to the products table.
            codes:      An iterable of secret code strings.

        Returns:
            The number of rows inserted.
        """
        items = [
            Inventory(product_id=product_id, data=code, is_sold=False)
            for code in codes
        ]
        self._session.add_all(items)
        await self._session.flush()
        return len(items)

    # ─────────────────────────────────────────────────────────
    #  READ
    # ─────────────────────────────────────────────────────────
    async def get_by_id(self, inventory_id: int) -> Optional[Inventory]:
        """Fetch a single inventory item by PK."""
        return await self._session.get(Inventory, inventory_id)

    async def count_available(self, product_id: int) -> int:
        """Return the number of unsold items for a product."""
        stmt = (
            select(func.count(Inventory.id))
            .where(
                Inventory.product_id == product_id,
                Inventory.is_sold.is_(False),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def count_sold(self, product_id: int) -> int:
        """Return the number of sold items for a product."""
        stmt = (
            select(func.count(Inventory.id))
            .where(
                Inventory.product_id == product_id,
                Inventory.is_sold.is_(True),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get_available(
        self,
        product_id: int,
        *,
        limit: int = 50,
    ) -> Sequence[Inventory]:
        """Return unsold items for a product (without locking)."""
        stmt = (
            select(Inventory)
            .where(
                Inventory.product_id == product_id,
                Inventory.is_sold.is_(False),
            )
            .order_by(Inventory.created_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ─────────────────────────────────────────────────────────
    #  🔒  LOCK & ACQUIRE  (the critical concurrency-safe path)
    # ─────────────────────────────────────────────────────────
    async def lock_available_item(
        self,
        product_id: int,
    ) -> Optional[Inventory]:
        """
        Atomically lock ONE unsold inventory row for purchase.

        Uses::

            SELECT *
              FROM inventory
             WHERE product_id = :pid
               AND is_sold = false
             ORDER BY created_at ASC
             LIMIT 1
               FOR UPDATE SKIP LOCKED

        **How this prevents overselling:**

        ``FOR UPDATE`` acquires a **row-level exclusive lock** on the
        selected row.  No other transaction can ``SELECT … FOR UPDATE``
        or ``UPDATE`` that same row until the lock is released (at
        ``COMMIT`` or ``ROLLBACK``).

        ``SKIP LOCKED`` tells PostgreSQL: *"If the row I would have
        picked is already locked by another transaction, don't wait —
        just skip it and try the next one."*

        Combined effect:
          • TX-A locks row #1 → gets it.
          • TX-B runs at the same time → row #1 is locked → TX-B
            **skips** it and locks row #2 instead.
          • Both transactions proceed in parallel, each with a
            **different** inventory item. No duplicate sales.

        If no unsold & unlocked rows remain, ``None`` is returned,
        meaning the product is genuinely out of stock.

        Args:
            product_id: The product whose stock to draw from.

        Returns:
            A locked ``Inventory`` instance, or ``None`` if out of stock.
        """
        stmt = (
            select(Inventory)
            .where(
                Inventory.product_id == product_id,
                Inventory.is_sold.is_(False),
            )
            .order_by(Inventory.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True, of=Inventory)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def lock_available_items(
        self,
        product_id: int,
        quantity: int,
    ) -> Sequence[Inventory]:
        """
        Lock up to ``quantity`` unsold rows for a bulk purchase.

        Same ``FOR UPDATE SKIP LOCKED`` semantics as
        ``lock_available_item``, but acquires multiple rows.

        If fewer than ``quantity`` rows are available, returns only
        as many as could be locked — the caller must check
        ``len(result) == quantity`` before proceeding.
        """
        stmt = (
            select(Inventory)
            .where(
                Inventory.product_id == product_id,
                Inventory.is_sold.is_(False),
            )
            .order_by(Inventory.created_at.asc())
            .limit(quantity)
            .with_for_update(skip_locked=True, of=Inventory)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ─────────────────────────────────────────────────────────
    #  UPDATE — mark as sold
    # ─────────────────────────────────────────────────────────
    async def mark_as_sold(
        self,
        inventory_id: int,
        buyer_id: int,
    ) -> None:
        """
        Flag an inventory item as sold.

        Must be called **within the same transaction** that
        acquired the lock via ``lock_available_item``.
        """
        stmt = (
            update(Inventory)
            .where(Inventory.id == inventory_id)
            .values(
                is_sold=True,
                sold_at=datetime.now(timezone.utc),
                buyer_id=buyer_id,
            )
        )
        await self._session.execute(stmt)

    async def mark_as_unsold(self, inventory_id: int) -> None:
        """
        Reverse a sale (e.g. on refund).

        Clears ``is_sold``, ``sold_at``, and ``buyer_id`` so the
        item goes back into the available pool.
        """
        stmt = (
            update(Inventory)
            .where(Inventory.id == inventory_id)
            .values(
                is_sold=False,
                sold_at=None,
                buyer_id=None,
            )
        )
        await self._session.execute(stmt)
