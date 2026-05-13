"""
Throttling middleware — rate-limits incoming messages per user via Redis.

Prevents spam by enforcing a minimum interval between messages.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message

from redis.asyncio import Redis


class ThrottlingMiddleware(BaseMiddleware):
    """
    Drop messages from users who exceed the rate limit.

    Uses Redis SETNX with TTL so it works across restarts.
    """

    def __init__(self, rate_limit: float = 0.5) -> None:
        super().__init__()
        self._rate_limit = rate_limit

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        # Retrieve the Redis instance from FSM storage
        redis: Redis | None = data.get("fsm_storage", {})
        if hasattr(data.get("bot"), "session"):
            # Try to get Redis from dispatcher storage
            storage = data.get("fsm_storage")
            if storage and hasattr(storage, "redis"):
                redis = storage.redis

        user_id = event.from_user.id
        key = f"throttle:{user_id}"

        # Use the FSM storage's redis or fall back to no throttling
        if isinstance(redis, Redis):
            throttled = await redis.set(
                key,
                "1",
                ex=int(self._rate_limit) or 1,
                nx=True,
            )
            if not throttled:
                # Key already existed → user is rate-limited
                return None

        return await handler(event, data)
