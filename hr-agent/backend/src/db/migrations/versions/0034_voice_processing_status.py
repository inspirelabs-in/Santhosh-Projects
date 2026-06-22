"""Voice-call result idempotency guard + provider_call_id uniqueness.

Two additive changes for the duplicate-evaluation bug class (V-C1/2/3, V-M5):

  * ``voice_calls.processing_status`` -- tracks OUR ingestion/evaluation of a
    call's RESULT (pending -> processing -> processed | failed), separate from the
    call lifecycle ``status``. Lets the webhook, the /recover endpoint, and the
    watchdog claim a result exactly once (atomic CAS) instead of all three
    re-running the evaluator.
  * Unique partial index on ``(provider, provider_call_id)`` WHERE
    provider_call_id IS NOT NULL -- so a duplicate provider id can never produce
    ``MultipleResultsFound`` (V-M5). Existing duplicates (should be none) are
    collapsed to the earliest row before the index is created.

Both additive / non-breaking.

Revision ID: 0034_voice_processing_status
Revises: 0033_role_company_context
Create Date: 2026-06-22
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_voice_processing_status"
down_revision: str | Sequence[str] | None = "0033_role_company_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. processing_status column. Existing rows default to 'pending'; rows that
    #    already have a transcript (result was ingested before this migration)
    #    are backfilled to 'processed' so the watchdog doesn't re-process them.
    op.add_column(
        "voice_calls",
        sa.Column(
            "processing_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.create_index(
        "ix_voice_calls_processing_status",
        "voice_calls",
        ["processing_status"],
    )
    op.execute(
        """
        UPDATE voice_calls
           SET processing_status = 'processed'
         WHERE transcript_r2_key IS NOT NULL
           AND transcript_r2_key <> ''
        """
    )

    # 2. Collapse any pre-existing duplicate provider_call_id rows (keep the
    #    earliest by created_at) so the unique index can be created safely.
    op.execute(
        """
        DELETE FROM voice_calls a
              USING voice_calls b
         WHERE a.provider_call_id IS NOT NULL
           AND a.provider_call_id = b.provider_call_id
           AND a.provider = b.provider
           AND a.created_at > b.created_at
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_voice_calls_provider_call_id
            ON voice_calls (provider, provider_call_id)
         WHERE provider_call_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_voice_calls_provider_call_id")
    op.drop_index("ix_voice_calls_processing_status", table_name="voice_calls")
    op.drop_column("voice_calls", "processing_status")
