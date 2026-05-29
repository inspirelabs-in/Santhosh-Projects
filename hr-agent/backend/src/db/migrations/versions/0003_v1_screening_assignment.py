"""v1: screening + assignment pipeline

V1 scope change: HR creates Role with assignment brief, agent generates screening
questions per candidate, evaluates responses, sends assignment on clear-pass,
parses submission, produces journey report. Drops interview/scheduling/cold-pool.

Adds:
  - roles.assignment_brief, assignment_instructions, assignment_deadline_days
  - applications.screening_questions (LLM-generated per applicant)
  - applications.screening_evaluation (LLM verdict + rationale)
  - applications.assignment_submission (files R2 keys + paste links + parse output)
  - applications.journey_report (markdown synthesis)
  - applications.current_stage (explicit state-machine column)

Revision ID: 0003_v1_screening_assignment
Revises: 0002_mail_dedup_and_feedback
Create Date: 2026-04-23
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003_v1_screening_assignment"
down_revision: str | Sequence[str] | None = "0002_mail_dedup_and_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("roles", sa.Column("assignment_brief", sa.Text, nullable=True))
    op.add_column("roles", sa.Column("assignment_instructions", sa.Text, nullable=True))
    op.add_column(
        "roles",
        sa.Column("assignment_deadline_days", sa.Integer, nullable=False, server_default="7"),
    )

    op.add_column("applications", sa.Column("screening_questions", JSONB, nullable=True))
    op.add_column("applications", sa.Column("screening_evaluation", JSONB, nullable=True))
    op.add_column("applications", sa.Column("assignment_submission", JSONB, nullable=True))
    op.add_column("applications", sa.Column("journey_report", sa.Text, nullable=True))
    op.add_column(
        "applications",
        sa.Column("current_stage", sa.String(50), nullable=False, server_default="applied"),
    )
    op.create_index("ix_applications_current_stage", "applications", ["current_stage"])


def downgrade() -> None:
    op.drop_index("ix_applications_current_stage", table_name="applications")
    op.drop_column("applications", "current_stage")
    op.drop_column("applications", "journey_report")
    op.drop_column("applications", "assignment_submission")
    op.drop_column("applications", "screening_evaluation")
    op.drop_column("applications", "screening_questions")
    op.drop_column("roles", "assignment_deadline_days")
    op.drop_column("roles", "assignment_instructions")
    op.drop_column("roles", "assignment_brief")
