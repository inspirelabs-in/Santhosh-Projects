"""voice_calls.call_kind enum + unique partial index on in-flight rows.

Two changes:

  * ``voice_calls.call_kind``  -- explicit "screening" | "confirmation" enum,
    replacing the brittle ``is_confirmation = not questions`` heuristic in
    the webhook handler.
  * Unique partial index on ``(application_id) WHERE status IN
    ('pending','dialing','in_progress')`` to prevent concurrent dispatchers
    from placing two calls to the same candidate.

Revision ID: 0014_voice_call_kind_and_inflight_unique
Revises: 0013_recruiter_memory
Create Date: 2026-05-11
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_voice_call_kind"
down_revision: str | Sequence[str] | None = "0013_recruiter_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # call_kind column. Default 'screening' for backfill; existing rows with
    # empty questions get retroactively flagged as 'confirmation' below.
    op.add_column(
        "voice_calls",
        sa.Column(
            "call_kind",
            sa.String(length=20),
            nullable=False,
            server_default="screening",
        ),
    )
    # Backfill: rows with no questions (null or empty list) are confirmation
    # calls under the old heuristic.
    op.execute(
        """
        UPDATE voice_calls
           SET call_kind = 'confirmation'
         WHERE questions IS NULL
            OR jsonb_typeof(questions::jsonb) = 'array'
           AND jsonb_array_length(questions::jsonb) = 0
        """
    )

    # Unique partial index: at most one in-flight row per application.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_voice_calls_application_in_flight
            ON voice_calls (application_id)
         WHERE status IN ('pending','dialing','in_progress')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_voice_calls_application_in_flight")
    op.drop_column("voice_calls", "call_kind")
