"""Add supervisor_experiments table for A/B testing.

Revision ID: 0022_experiments
Revises: 0021_confidence_gate_policies
Create Date: 2026-06-12
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0022_experiments"
down_revision: str | None = "0021_confidence_gate_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supervisor_experiments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("variants", JSONB, server_default="[]"),
        sa.Column("target_stage", sa.String(64), nullable=True),
        sa.Column("target_event_types", JSONB, server_default="[]"),
        sa.Column("sample_rate", sa.Float, server_default="1.0"),
        sa.Column("status", sa.String(16), server_default="'active'"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("concluded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conclusion_notes", sa.Text, nullable=True),
    )
    op.create_index("ix_supervisor_experiments_status", "supervisor_experiments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_supervisor_experiments_status")
    op.drop_table("supervisor_experiments")
