"""Add panel_members table for workspace-level interview panel directory.

HR adds members once via /settings/panels. Roles reference them by id when
configuring per-round panels (technical/hr/ceo/founder).

Revision ID: 0007_panel_members
Revises: 0006_admin_review_column
Create Date: 2026-04-30
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_panel_members"
down_revision: str | Sequence[str] | None = "0006_admin_review_column"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "panel_members",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role_type", sa.String(20), nullable=False),
        sa.Column("job_title", sa.String(255)),
        sa.Column(
            "timezone",
            sa.String(64),
            nullable=False,
            server_default="Asia/Kolkata",
        ),
        sa.Column(
            "calendar_provider",
            sa.String(20),
            nullable=False,
            server_default="microsoft",
        ),
        sa.Column("calendar_id", sa.String(255)),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("notes", sa.Text()),
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
        sa.UniqueConstraint("email", name="uq_panel_members_email"),
    )
    op.create_index(
        "ix_panel_members_email", "panel_members", ["email"], unique=False
    )
    op.create_index(
        "ix_panel_members_role_type",
        "panel_members",
        ["role_type"],
        unique=False,
    )
    op.create_index(
        "ix_panel_members_is_active",
        "panel_members",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        "ix_panel_members_role_active",
        "panel_members",
        ["role_type", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_panel_members_role_active", table_name="panel_members")
    op.drop_index("ix_panel_members_is_active", table_name="panel_members")
    op.drop_index("ix_panel_members_role_type", table_name="panel_members")
    op.drop_index("ix_panel_members_email", table_name="panel_members")
    op.drop_table("panel_members")
