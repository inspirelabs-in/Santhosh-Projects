"""Add deadline_at and reminder_sent_at to assignments table.

Revision ID: 0027_assignment_deadline
Revises: 0026_panel_expertise
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0027_assignment_deadline"
down_revision: str = "0026_panel_expertise"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assignments",
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assignments",
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assignments", "reminder_sent_at")
    op.drop_column("assignments", "deadline_at")
