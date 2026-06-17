"""Add pipeline_template JSONB column to roles table.

Stores an ordered list of pipeline step IDs per role, enabling
configurable hiring flows (e.g. ["fit_score", "voice_screen",
"technical_interview", "offer"]). NULL means use legacy hardcoded flow.

Revision ID: 0024_pipeline_template
Revises: 0023_voice_campaigns
Create Date: 2026-06-15
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0024_pipeline_template"
down_revision: str | Sequence[str] | None = "0023_voice_campaigns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("roles", sa.Column("pipeline_template", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("roles", "pipeline_template")
