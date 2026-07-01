"""Add PI assessment link columns to roles table.

Revision ID: 0038_role_pi_links
Revises: 0037_org_inspirelabs
Create Date: 2026-06-30
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_role_pi_links"
down_revision: str | Sequence[str] | None = "0037_org_inspirelabs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("roles", sa.Column("pi_cognitive_link", sa.Text(), nullable=True))
    op.add_column("roles", sa.Column("pi_personality_link", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("roles", "pi_personality_link")
    op.drop_column("roles", "pi_cognitive_link")
