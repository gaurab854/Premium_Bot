"""
PromoCodeRepository — all DB queries for promo_codes and promo_order_requests.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.promo_code import PromoCode
from database.models.promo_order_request import PromoOrderRequest, PromoOrderStatus


class PromoCodeRepository:
    """CRUD for PromoCode and PromoOrderRequest tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─────────────────────────────────────────────────────────
    #  PromoCode CRUD
    # ─────────────────────────────────────────────────────────

    async def create_code(
        self,
        code: str,
        *,
        description: Optional[str] = None,
        product_id: Optional[int] = None,
        max_uses: int = 1,
        requires_approval: bool = True,
    ) -> PromoCode:
        """Create a new promo code."""
        obj = PromoCode(
            code=code.upper().strip(),
            description=description,
            product_id=product_id,
            max_uses=max_uses,
            requires_approval=requires_approval,
        )
        self._session.add(obj)
        await self._session.flush()
        await self._session.refresh(obj)
        return obj

    async def get_code(self, code: str) -> Optional[PromoCode]:
        """Fetch a promo code by its string (case-insensitive)."""
        stmt = select(PromoCode).where(
            PromoCode.code == code.upper().strip(),
            PromoCode.is_active.is_(True),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_codes(self) -> Sequence[PromoCode]:
        """Return all promo codes (active and inactive)."""
        stmt = select(PromoCode).order_by(PromoCode.created_at.desc())
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def increment_used(self, code_id: int) -> None:
        """Increment the used_count for a promo code."""
        stmt = (
            update(PromoCode)
            .where(PromoCode.id == code_id)
            .values(used_count=PromoCode.used_count + 1)
        )
        await self._session.execute(stmt)

    async def deactivate_code(self, code_id: int) -> None:
        """Permanently disable a promo code."""
        stmt = (
            update(PromoCode)
            .where(PromoCode.id == code_id)
            .values(is_active=False)
        )
        await self._session.execute(stmt)

    # ─────────────────────────────────────────────────────────
    #  PromoOrderRequest CRUD
    # ─────────────────────────────────────────────────────────

    async def create_request(
        self,
        user_id: int,
        product_id: int,
        promo_code: str,
    ) -> PromoOrderRequest:
        """Create a pending promo-code order request."""
        obj = PromoOrderRequest(
            user_id=user_id,
            product_id=product_id,
            promo_code=promo_code.upper().strip(),
            status=PromoOrderStatus.PENDING,
        )
        self._session.add(obj)
        await self._session.flush()
        await self._session.refresh(obj)
        return obj

    async def get_request(self, request_id: int) -> Optional[PromoOrderRequest]:
        """Fetch a promo order request by PK."""
        return await self._session.get(PromoOrderRequest, request_id)

    async def set_admin_message(
        self,
        request_id: int,
        admin_chat_id: int,
        admin_message_id: int,
    ) -> None:
        """Store the admin notification message reference for later editing."""
        stmt = (
            update(PromoOrderRequest)
            .where(PromoOrderRequest.id == request_id)
            .values(
                admin_chat_id=admin_chat_id,
                admin_message_id=admin_message_id,
            )
        )
        await self._session.execute(stmt)

    async def approve_request(
        self,
        request_id: int,
        *,
        note: Optional[str] = None,
    ) -> bool:
        """
        Transition PENDING → APPROVED.
        Returns True if updated, False if already processed (idempotent).
        """
        stmt = (
            update(PromoOrderRequest)
            .where(
                PromoOrderRequest.id == request_id,
                PromoOrderRequest.status == PromoOrderStatus.PENDING,
            )
            .values(status=PromoOrderStatus.APPROVED, admin_note=note)
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

    async def reject_request(
        self,
        request_id: int,
        *,
        note: Optional[str] = None,
    ) -> bool:
        """
        Transition PENDING → REJECTED.
        Returns True if updated, False if already processed (idempotent).
        """
        stmt = (
            update(PromoOrderRequest)
            .where(
                PromoOrderRequest.id == request_id,
                PromoOrderRequest.status == PromoOrderStatus.PENDING,
            )
            .values(status=PromoOrderStatus.REJECTED, admin_note=note)
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1
