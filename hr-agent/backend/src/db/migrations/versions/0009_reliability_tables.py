"""Reliability + observability tables.

* email_sends   -- idempotent send tracking; (application_id, template) unique
* pipeline_alerts -- raised when an application is stuck past SLA
* candidates.timezone -- IANA tz captured at apply-time

Revision ID: 0009_reliability_tables
Revises: 0008_config_settings
Create Date: 2026-05-07
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_reliability_tables"
down_revision: str | Sequence[str] | None = "0008_config_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_sends",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("to_email", sa.String(320), nullable=False),
        sa.Column("template", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="sent"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_email_sends_idempotency_key"),
    )
    op.create_index("ix_email_sends_application_id", "email_sends", ["application_id"])
    op.create_index("ix_email_sends_to_email", "email_sends", ["to_email"])
    op.create_index("ix_email_sends_created_at", "email_sends", ["created_at"])

    op.create_table(
        "pipeline_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alert_type", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(64), nullable=True),
        sa.Column("hours_stuck", sa.Float, nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_pipeline_alerts_application_id", "pipeline_alerts", ["application_id"])
    op.create_index("ix_pipeline_alerts_unresolved", "pipeline_alerts", ["application_id", "resolved_at"])

    op.add_column("candidates", sa.Column("timezone", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("candidates", "timezone")
    op.drop_index("ix_pipeline_alerts_unresolved", table_name="pipeline_alerts")
    op.drop_index("ix_pipeline_alerts_application_id", table_name="pipeline_alerts")
    op.drop_table("pipeline_alerts")
    op.drop_index("ix_email_sends_created_at", table_name="email_sends")
    op.drop_index("ix_email_sends_to_email", table_name="email_sends")
    op.drop_index("ix_email_sends_application_id", table_name="email_sends")
    op.drop_table("email_sends")
