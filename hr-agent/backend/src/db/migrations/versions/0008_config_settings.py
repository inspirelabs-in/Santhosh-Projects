"""Add config_settings + config_audit tables for runtime-editable config.

Lets HR admins manage LLM keys, integrations, feature flags, thresholds, and
branding from the dashboard without touching .env files. Secret values are
encrypted at rest via Fernet (see services/config_crypto.py).

Revision ID: 0008_config_settings
Revises: 0007_panel_members
Create Date: 2026-04-30
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_config_settings"
down_revision: str | Sequence[str] | None = "0007_panel_members"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "config_settings",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "is_secret",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )

    op.create_table(
        "config_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.String(512)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_config_audit_key", "config_audit", ["key", "created_at"], unique=False
    )
    op.create_index(
        "ix_config_audit_created", "config_audit", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_config_audit_created", table_name="config_audit")
    op.drop_index("ix_config_audit_key", table_name="config_audit")
    op.drop_table("config_audit")
    op.drop_table("config_settings")
