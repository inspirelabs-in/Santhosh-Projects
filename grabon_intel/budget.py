"""Atomic daily cost ledger.

Single-row-per-day. Debits are atomic via `UPDATE ... RETURNING` with a
conditional `WHERE spent_cents + :delta <= cap_cents`. Returns the new
balance, or `None` if the debit would breach the cap.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings


async def ensure_today(session: AsyncSession, cap_cents: int | None = None) -> None:
    cap = cap_cents if cap_cents is not None else get_settings().daily_cost_cap_cents
    await session.execute(
        text(
            "INSERT INTO budget (day, spent_cents, cap_cents) "
            "VALUES (CURRENT_DATE, 0, :cap) ON CONFLICT (day) DO NOTHING"
        ),
        {"cap": cap},
    )


async def try_debit(session: AsyncSession, cents: int) -> int | None:
    """Atomically add `cents` to today's spend if it stays under cap.

    Returns the new spent_cents on success, or None if it would exceed cap.
    """
    if cents <= 0:
        return 0
    await ensure_today(session)
    row = (
        await session.execute(
            text(
                "UPDATE budget SET spent_cents = spent_cents + :d, updated_at = NOW() "
                "WHERE day = CURRENT_DATE AND spent_cents + :d <= cap_cents "
                "RETURNING spent_cents"
            ),
            {"d": cents},
        )
    ).first()
    return int(row[0]) if row else None


async def refund(session: AsyncSession, cents: int) -> int:
    """Decrement today's spend (never below zero). Used to release a reservation."""
    if cents <= 0:
        return 0
    await ensure_today(session)
    row = (
        await session.execute(
            text(
                "UPDATE budget SET spent_cents = GREATEST(0, spent_cents - :d), "
                "updated_at = NOW() WHERE day = CURRENT_DATE RETURNING spent_cents"
            ),
            {"d": cents},
        )
    ).first()
    return int(row[0]) if row else 0


async def get_today(session: AsyncSession) -> tuple[int, int]:
    await ensure_today(session)
    row = (
        await session.execute(
            text("SELECT spent_cents, cap_cents FROM budget WHERE day = CURRENT_DATE")
        )
    ).first()
    return (int(row[0]), int(row[1])) if row else (0, 0)


class BudgetExceeded(RuntimeError):
    def __init__(self, attempted_cents: int, spent: int, cap: int) -> None:
        super().__init__(
            f"daily budget cap reached: attempted={attempted_cents}c spent={spent}c cap={cap}c"
        )
        self.attempted_cents = attempted_cents
        self.spent = spent
        self.cap = cap


async def debit_or_raise(session: AsyncSession, cents: int) -> int:
    new_balance = await try_debit(session, cents)
    if new_balance is None:
        spent, cap = await get_today(session)
        raise BudgetExceeded(cents, spent, cap)
    return new_balance


__all__ = ["ensure_today", "try_debit", "refund", "get_today", "debit_or_raise", "BudgetExceeded"]
_ = dt  # keep import for downstream type stubs
