"""
UserRepository — all database queries for the ``users`` table.

Design notes:
  • Every public method receives only primitive arguments (int, str, bool)
    so the repository is trivially testable with a mocked session.
  • ``get_or_create`` uses PostgreSQL ON CONFLICT … DO UPDATE (upsert)
    to atomically register new Telegram users or refresh stale profile data.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.user import User


class UserRepository:
    """Encapsulates every database operation on the ``User`` model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─────────────────────────────────────────────────────────
    #  CREATE / UPSERT
    # ─────────────────────────────────────────────────────────
    async def get_or_create(
        self,
        telegram_id: int,
        first_name: str,
        last_name: Optional[str] = None,
        username: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> User:
        """
        Register a new user **or** refresh an existing user's profile.

        Uses PostgreSQL ``INSERT … ON CONFLICT (telegram_id) DO UPDATE``
        so concurrent /start commands never raise a unique-violation.

        Returns:
            The created or updated ``User`` instance.
        """
        stmt = (
            pg_insert(User)
            .values(
                telegram_id=telegram_id,
                first_name=first_name,
                last_name=last_name,
                username=username,
                language_code=language_code,
            )
            .on_conflict_do_update(
                index_elements=[User.telegram_id],
                set_={
                    "first_name": first_name,
                    "last_name": last_name,
                    "username": username,
                    "language_code": language_code,
                },
            )
            .returning(User)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    # ─────────────────────────────────────────────────────────
    #  READ — single record
    # ─────────────────────────────────────────────────────────
    async def get_by_id(self, user_id: int) -> Optional[User]:
        """Fetch a user by their internal DB primary key."""
        return await self._session.get(User, user_id)

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Fetch a user by their Telegram ID (the unique business key)."""
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists(self, telegram_id: int) -> bool:
        """Return ``True`` if a user with the given Telegram ID exists."""
        stmt = (
            select(func.count(User.id))
            .where(User.telegram_id == telegram_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one() > 0

    # ─────────────────────────────────────────────────────────
    #  READ — multiple records
    # ─────────────────────────────────────────────────────────
    async def get_all_active(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[User]:
        """Return active (non-banned) users with pagination."""
        stmt = (
            select(User)
            .where(User.is_active.is_(True))
            .order_by(User.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_admins(self) -> Sequence[User]:
        """Return all users flagged as bot admins."""
        stmt = (
            select(User)
            .where(User.is_admin.is_(True))
            .order_by(User.id)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_active(self) -> int:
        """Return the total number of active users."""
        stmt = select(func.count(User.id)).where(User.is_active.is_(True))
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def count_all(self) -> int:
        """Return the total number of users (including banned)."""
        stmt = select(func.count(User.id))
        result = await self._session.execute(stmt)
        return result.scalar_one()

    # ─────────────────────────────────────────────────────────
    #  UPDATE
    # ─────────────────────────────────────────────────────────
    async def set_active(self, telegram_id: int, *, active: bool) -> None:
        """Soft-ban (``active=False``) or re-activate a user."""
        stmt = (
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(is_active=active)
        )
        await self._session.execute(stmt)

    async def set_admin(self, telegram_id: int, *, is_admin: bool) -> None:
        """Promote or demote a user to/from bot admin."""
        stmt = (
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(is_admin=is_admin)
        )
        await self._session.execute(stmt)
