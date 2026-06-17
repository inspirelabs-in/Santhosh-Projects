"""Add negotiation_state JSONB column to meeting_sessions.

Revision ID: 0025_meeting_negotiation_state
Revises: 0024_pipeline_template
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0025_meeting_negotiation_state"
down_revision: str | Sequence[str] | None = "0024_pipeline_template"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meeting_sessions", sa.Column("negotiation_state", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("meeting_sessions", "negotiation_state")
