"""
DepositRepository — all database queries for the ``deposits`` table.

Handles creation of pending deposits, status transitions,
and duplicate TXID detection.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.deposit import Deposit, DepositStatus


class DepositRepository:
    """Encapsulates every database operation on the ``Deposit`` model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─────────────────────────────────────────────────────────
    #  CREATE
    # ─────────────────────────────────────────────────────────
    async def create(
        self,
        user_id: int,
        txid: str,
        amount: float,
    ) -> Deposit:
        """
        Create a new deposit with ``PENDING`` status.

        Args:
            user_id: Internal DB user ID (not telegram_id).
            txid:    The blockchain / payment transaction ID.
            amount:  Deposit amount in USD.

        Returns:
            The newly created ``Deposit`` instance.
        """
        deposit = Deposit(
            user_id=user_id,
            txid=txid,
            amount=amount,
            status=DepositStatus.PENDING,
        )
        self._session.add(deposit)
        await self._session.flush()
        await self._session.refresh(deposit)
        return deposit

    # ─────────────────────────────────────────────────────────
    #  READ
    # ─────────────────────────────────────────────────────────
    async def get_by_id(self, deposit_id: int) -> Optional[Deposit]:
        """Fetch a deposit by its primary key (eagerly loads user)."""
        stmt = (
            select(Deposit)
            .where(Deposit.id == deposit_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_txid(self, txid: str) -> Optional[Deposit]:
        """Check if a TXID has already been submitted."""
        stmt = select(Deposit).where(Deposit.txid == txid)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_pending(self, *, limit: int = 50) -> Sequence[Deposit]:
        """Return all deposits waiting for admin review."""
        stmt = (
            select(Deposit)
            .where(Deposit.status == DepositStatus.PENDING)
            .order_by(Deposit.created_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_by_user(
        self,
        user_id: int,
        *,
        limit: int = 20,
    ) -> Sequence[Deposit]:
        """Return a user's deposit history (newest first)."""
        stmt = (
            select(Deposit)
            .where(Deposit.user_id == user_id)
            .order_by(Deposit.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_pending(self) -> int:
        """Return the number of pending deposits."""
        stmt = (
            select(func.count(Deposit.id))
            .where(Deposit.status == DepositStatus.PENDING)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    # ─────────────────────────────────────────────────────────
    #  STATUS TRANSITIONS
    # ─────────────────────────────────────────────────────────
    async def set_status(
        self,
        deposit_id: int,
        new_status: DepositStatus,
        *,
        note: Optional[str] = None,
    ) -> bool:
        """
        Transition a deposit's status, but **only if it is still PENDING**.

        The ``WHERE status = 'pending'`` clause ensures that once a
        deposit has been approved or rejected it cannot be changed
        again — this is the key to preventing double-crediting.

        Returns:
            ``True``  if the status was updated (rowcount == 1).
            ``False`` if the deposit was already processed.
        """
        values: dict = {"status": new_status}
        if note is not None:
            values["note"] = note

        stmt = (
            update(Deposit)
            .where(
                Deposit.id == deposit_id,
                Deposit.status == DepositStatus.PENDING,  # ← idempotency guard
            )
            .values(**values)
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

    async def approve(
        self,
        deposit_id: int,
        *,
        note: Optional[str] = None,
    ) -> bool:
        """Convenience wrapper: mark a pending deposit as CONFIRMED."""
        return await self.set_status(
            deposit_id,
            DepositStatus.CONFIRMED,
            note=note,
        )

    async def reject(
        self,
        deposit_id: int,
        *,
        note: Optional[str] = None,
    ) -> bool:
        """Convenience wrapper: mark a pending deposit as REJECTED."""
        return await self.set_status(
            deposit_id,
            DepositStatus.REJECTED,
            note=note,
        )
