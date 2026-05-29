"""Add roles.screening_modality.

HR picks per role at creation time:
    chat   -> agentic chat-first screening (V2 default)
    voice  -> AI phone-screen (V1 voice path)

Existing rows default to 'chat' so the V2 flow is opt-out, not opt-in.

Revision ID: 0011_role_screening_modality
Revises: 0010_chat_v2_tables
Create Date: 2026-05-10
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_role_screening_modality"
down_revision: str | Sequence[str] | None = "0010_chat_v2_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "screening_modality",
            sa.String(16),
            nullable=False,
            server_default="chat",
        ),
    )
    op.create_index(
        "ix_roles_screening_modality", "roles", ["screening_modality"]
    )


def downgrade() -> None:
    op.drop_index("ix_roles_screening_modality", table_name="roles")
    op.drop_column("roles", "screening_modality")
