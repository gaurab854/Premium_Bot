"""
User service — business logic on top of UserRepository.

Keeps handlers thin by encapsulating complex workflows here.
"""

from __future__ import annotations

from typing import Optional

from database.models.user import User
from database.repositories.user import UserRepository


class UserService:
    """Business logic for user-related operations."""

    def __init__(self, repo: UserRepository) -> None:
        self._repo = repo

    async def register_or_update(
        self,
        telegram_id: int,
        first_name: str,
        last_name: Optional[str] = None,
        username: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> User:
        """Register a new user or update their profile."""
        return await self._repo.upsert(
            telegram_id=telegram_id,
            first_name=first_name,
            last_name=last_name,
            username=username,
            language_code=language_code,
        )

    async def get_profile(self, telegram_id: int) -> Optional[User]:
        """Retrieve a user's profile."""
        return await self._repo.get_by_telegram_id(telegram_id)

    async def ban_user(self, telegram_id: int) -> None:
        """Soft-ban a user."""
        await self._repo.set_active(telegram_id, active=False)

    async def unban_user(self, telegram_id: int) -> None:
        """Re-activate a banned user."""
        await self._repo.set_active(telegram_id, active=True)

    async def get_active_user_count(self) -> int:
        """Return total number of active users."""
        return await self._repo.count_active()
