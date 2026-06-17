"""Add expertise and capacity fields to panel_members.

Revision ID: 0026_panel_expertise
Revises: 0025_meeting_negotiation_state
Create Date: 2026-06-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0026_panel_expertise"
down_revision: str = "0025_meeting_negotiation_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("panel_members", sa.Column("expertise_tags", JSONB, nullable=True))
    op.add_column("panel_members", sa.Column("department", sa.String(100), nullable=True))
    op.add_column(
        "panel_members",
        sa.Column("max_interviews_per_week", sa.Integer, server_default="10", nullable=False),
    )
    op.add_column("panel_members", sa.Column("seniority_level", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("panel_members", "seniority_level")
    op.drop_column("panel_members", "max_interviews_per_week")
    op.drop_column("panel_members", "department")
    op.drop_column("panel_members", "expertise_tags")
