"""
AdminLog model — audit trail of all administrative actions.

Every admin action (ban, unban, deposit approval, product edit, etc.)
is recorded here for accountability and debugging.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base

if TYPE_CHECKING:
    from database.models.user import User


class AdminLog(Base):
    """Immutable log entry for an admin action."""

    __tablename__ = "admin_logs"

    # ── Who performed the action ──────────────────────────────
    admin_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Admin user who performed the action",
    )

    # ── What happened ─────────────────────────────────────────
    action: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        comment="Action identifier (e.g. 'ban_user', 'approve_deposit')",
    )
    details: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="JSON or free-text details about the action",
    )

    # ── Who was affected (optional) ───────────────────────────
    target_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User affected by the admin action (if applicable)",
    )

    # ── Relationships ─────────────────────────────────────────
    admin: Mapped["User"] = relationship(
        "User",
        back_populates="admin_logs",
        foreign_keys=[admin_id],
        lazy="joined",
    )
    target_user: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[target_user_id],
        lazy="joined",
    )
