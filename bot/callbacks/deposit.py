"""
Deposit CallbackData factory.

Encodes the deposit ID and the admin's decision (approve / reject)
into a compact callback string like ``deposit:42:approve``.

Aiogram's ``CallbackData`` class handles serialisation, deserialisation,
and validation automatically — no manual string splitting needed.
"""

from __future__ import annotations

from enum import Enum

from aiogram.filters.callback_data import CallbackData


class DepositAction(str, Enum):
    """Possible admin actions on a deposit."""

    APPROVE = "approve"
    REJECT = "reject"


class DepositCallback(CallbackData, prefix="deposit"):
    """
    Typed callback payload for deposit approve/reject buttons.

    Serialises to: ``deposit:<deposit_id>:<action>``

    Fields:
        deposit_id: Primary key of the ``deposits`` row.
        action:     ``approve`` or ``reject``.
    """

    deposit_id: int
    action: DepositAction
