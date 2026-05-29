"""mail dedup + interview feedback

Adds:
  - processed_messages: dedup guard for inbound mail Message-Id
  - interviews.feedback: free-form HR feedback captured after the interview
    (stored alongside the existing `report` column which is the LLM summary)

Revision ID: 0002_mail_dedup_and_feedback
Revises: 0001_initial_schema
Create Date: 2026-04-21
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0002_mail_dedup_and_feedback"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_messages",
        sa.Column("message_id", sa.String(length=512), primary_key=True),
        sa.Column("application_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("mail_source", sa.String(length=32)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.add_column(
        "interviews",
        sa.Column("feedback", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("interviews", "feedback")
    op.drop_table("processed_messages")
