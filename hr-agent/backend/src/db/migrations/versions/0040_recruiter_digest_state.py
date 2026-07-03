"""Presence/digest bookkeeping for the welcome-back recruiter digest.

Revision ID: 0040_recruiter_digest_state
Revises: 0039_embedding_dim_256
Create Date: 2026-07-02
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_recruiter_digest_state"
down_revision: str | Sequence[str] | None = "0039_embedding_dim_256"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recruiter_digest_state",
        sa.Column("actor_hash", sa.String(length=64), primary_key=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_digest_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("recruiter_digest_state")
