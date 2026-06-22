"""Recruiter artifacts: structured, editable artifacts per conversation.

Backs the artifact side-panel that replaces the inline confirm-card block. The
agent writes typed JSON (e.g. a role_draft) here; the frontend renders it as an
editable form. Additive / non-breaking.

Revision ID: 0032_recruiter_artifacts
Revises: 0031_phase0_foundation
Create Date: 2026-06-19
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

revision: str = "0032_recruiter_artifacts"
down_revision: str | Sequence[str] | None = "0031_phase0_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recruiter_artifacts",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "conversation_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("recruiter_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_id", PG_UUID(as_uuid=True)),
        sa.Column("application_id", PG_UUID(as_uuid=True)),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default="draft"),
        sa.Column("title", sa.String(255)),
        sa.Column("content", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_recruiter_artifacts_conversation_id", "recruiter_artifacts", ["conversation_id"])
    op.create_index("ix_recruiter_artifacts_org_id", "recruiter_artifacts", ["org_id"])
    op.create_index("ix_recruiter_artifacts_application_id", "recruiter_artifacts", ["application_id"])
    op.create_index("ix_recruiter_artifacts_conv_status", "recruiter_artifacts", ["conversation_id", "status"])


def downgrade() -> None:
    op.drop_table("recruiter_artifacts")
