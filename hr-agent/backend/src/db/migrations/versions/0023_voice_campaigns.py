"""Add voice_campaigns table and campaign_id FK on voice_calls.

Supports bulk outbound voice call campaigns for multi-purpose calling
(status updates, meeting scheduling, joining details, etc.).

Revision ID: 0023_voice_campaigns
Revises: 0022_experiments
Create Date: 2026-06-15
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0023_voice_campaigns"
down_revision: str | Sequence[str] | None = "0022_experiments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "voice_campaigns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("role_id", UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="SET NULL"), index=True, nullable=True),
        sa.Column("call_kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False, index=True),
        sa.Column("total_calls", sa.Integer, server_default="0", nullable=False),
        sa.Column("completed_calls", sa.Integer, server_default="0", nullable=False),
        sa.Column("failed_calls", sa.Integer, server_default="0", nullable=False),
        sa.Column("max_concurrent", sa.Integer, server_default="10", nullable=False),
        sa.Column("dispatch_rate_per_minute", sa.Integer, server_default="5", nullable=False),
        sa.Column("target_application_ids", sa.JSON, nullable=True),
        sa.Column("context_template", sa.JSON, nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "voice_calls",
        sa.Column(
            "campaign_id",
            UUID(as_uuid=True),
            sa.ForeignKey("voice_campaigns.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("voice_calls", "campaign_id")
    op.drop_table("voice_campaigns")
