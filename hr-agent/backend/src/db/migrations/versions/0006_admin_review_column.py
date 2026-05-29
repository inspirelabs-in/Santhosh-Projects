"""Add applications.admin_review JSONB for per-round admin gate decisions.

Stores structured reviewer inputs keyed by round ("assessment", "technical",
"ceo", "hr"). Each value carries decision, notes, and round-specific fields
(PI persona + PI assessment link for the assessment round).

Pipeline stages are still VARCHAR + validated in application code via
``models.v1.PipelineStage`` + ``ALLOWED_TRANSITIONS`` -- no enum-type
migration needed for the new ``*_pending_*`` and ``hr_meeting_*`` values.

Revision ID: 0006_admin_review_column
Revises: 0005_agentic_v2_tables
Create Date: 2026-04-28
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_admin_review_column"
down_revision: str | Sequence[str] | None = "0005_agentic_v2_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("admin_review", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Backfill: roles.pi_cognitive_url was added to the ORM model earlier but
    # never got its own migration. Idempotent add here.
    bind = op.get_bind()
    cols = {
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='roles'"
        )
    }
    if "pi_cognitive_url" not in cols:
        op.add_column(
            "roles",
            sa.Column("pi_cognitive_url", sa.String(length=1000), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("applications", "admin_review")
    bind = op.get_bind()
    cols = {
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='roles'"
        )
    }
    if "pi_cognitive_url" in cols:
        op.drop_column("roles", "pi_cognitive_url")
