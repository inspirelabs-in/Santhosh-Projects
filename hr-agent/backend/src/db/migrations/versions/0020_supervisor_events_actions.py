"""Add supervisor_events and supervisor_actions tables (Phase 3).

Revision ID: 0020_supervisor
Revises: 0019_contradiction_tolerances
Create Date: 2026-06-12
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0020_supervisor"
down_revision: str = "0019_contradiction_tolerances"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supervisor_events",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("application_id", UUID(as_uuid=True)),
        sa.Column("candidate_id", UUID(as_uuid=True)),
        sa.Column("payload", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("dedup_key", sa.String(255), unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("claimed_by", sa.String(128)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_supervisor_events_status_created", "supervisor_events", ["status", "created_at"])
    op.create_index("ix_supervisor_events_app", "supervisor_events", ["application_id"])

    op.create_table(
        "supervisor_actions",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("supervisor_events.id"), nullable=False),
        sa.Column("application_id", UUID(as_uuid=True)),
        sa.Column("candidate_id", UUID(as_uuid=True)),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("action_params", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reasoning", sa.Text),
        sa.Column("confidence", sa.Float),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("executed", sa.Boolean, server_default=sa.text("false")),
        sa.Column("execution_result", JSONB),
        sa.Column("evidence_ids", JSONB),
        sa.Column("policy_rule_ids", JSONB),
        sa.Column("approved_by", sa.String(128)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_by", sa.String(128)),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("decision_record_id", UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_supervisor_actions_event", "supervisor_actions", ["event_id"])
    op.create_index("ix_supervisor_actions_app", "supervisor_actions", ["application_id"])


def downgrade() -> None:
    op.drop_table("supervisor_actions")
    op.drop_table("supervisor_events")
