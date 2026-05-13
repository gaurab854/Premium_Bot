"""
WalletRepository — all database queries for the ``wallets`` table.

Design notes:
  • Balance mutations NEVER use read-then-write in Python.
    Instead they use SQL-level arithmetic::

        UPDATE wallets SET balance = balance + :delta WHERE …

    This makes every credit / debit an **atomic** operation that is safe
    under concurrent access without application-level locking.

  • ``deduct_balance`` adds a ``WHERE balance >= amount`` guard so the
    row is only updated when sufficient funds exist — the caller checks
    the rowcount to know if the deduction succeeded.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.wallet import Wallet


class WalletRepository:
    """Encapsulates every database operation on the ``Wallet`` model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─────────────────────────────────────────────────────────
    #  CREATE / GET
    # ─────────────────────────────────────────────────────────
    async def get_by_user_id(self, user_id: int) -> Optional[Wallet]:
        """Fetch the wallet belonging to a specific user."""
        stmt = select(Wallet).where(Wallet.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create(self, user_id: int) -> Wallet:
        """
        Return the user's wallet, creating one with zero balance
        if it does not exist yet.

        This is intentionally *not* an upsert — wallets are created
        once and never conflict because of the unique constraint.
        """
        wallet = await self.get_by_user_id(user_id)
        if wallet is not None:
            return wallet

        wallet = Wallet(user_id=user_id, balance=0.0)
        self._session.add(wallet)
        await self._session.flush()       # assigns wallet.id
        await self._session.refresh(wallet)
        return wallet

    async def get_balance(self, user_id: int) -> float:
        """Return the current balance for a user (0.0 if no wallet)."""
        wallet = await self.get_by_user_id(user_id)
        return wallet.balance if wallet else 0.0

    # ─────────────────────────────────────────────────────────
    #  BALANCE MUTATIONS (atomic SQL-level arithmetic)
    # ─────────────────────────────────────────────────────────
    async def add_balance(self, wallet_id: int, amount: float) -> Wallet:
        """
        Credit a wallet by ``amount`` using an atomic SQL increment.

        .. code-block:: sql

            UPDATE wallets
               SET balance = balance + :amount
             WHERE id = :wallet_id

        Args:
            wallet_id: Primary key of the wallet to credit.
            amount:    Positive value to add.

        Returns:
            The refreshed ``Wallet`` instance with the new balance.

        Raises:
            ValueError: If ``amount`` is not positive.
        """
        if amount <= 0:
            raise ValueError(f"Credit amount must be positive, got {amount}")

        stmt = (
            update(Wallet)
            .where(Wallet.id == wallet_id)
            .values(balance=Wallet.balance + amount)
            .returning(Wallet)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def deduct_balance(self, wallet_id: int, amount: float) -> bool:
        """
        Debit a wallet by ``amount`` **only if sufficient funds exist**.

        Uses a single atomic UPDATE with a ``WHERE`` guard::

            UPDATE wallets
               SET balance = balance - :amount
             WHERE id = :wallet_id
               AND balance >= :amount        ← prevents negative balance

        Args:
            wallet_id: Primary key of the wallet to debit.
            amount:    Positive value to subtract.

        Returns:
            ``True``  — deduction succeeded (rowcount == 1).
            ``False`` — insufficient funds (rowcount == 0).

        Raises:
            ValueError: If ``amount`` is not positive.
        """
        if amount <= 0:
            raise ValueError(f"Debit amount must be positive, got {amount}")

        stmt = (
            update(Wallet)
            .where(
                Wallet.id == wallet_id,
                Wallet.balance >= amount,     # ← the safety guard
            )
            .values(balance=Wallet.balance - amount)
        )
        result = await self._session.execute(stmt)

        # result.rowcount == 1 means the row was updated (funds sufficient)
        # result.rowcount == 0 means the WHERE failed (insufficient funds)
        return result.rowcount == 1

    async def set_balance(self, wallet_id: int, new_balance: float) -> Wallet:
        """
        Hard-set the wallet balance (admin override / correction).

        Should only be called from admin operations; prefer
        ``add_balance`` / ``deduct_balance`` for normal flows.
        """
        if new_balance < 0:
            raise ValueError(f"Balance cannot be negative, got {new_balance}")

        stmt = (
            update(Wallet)
            .where(Wallet.id == wallet_id)
            .values(balance=new_balance)
            .returning(Wallet)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()
