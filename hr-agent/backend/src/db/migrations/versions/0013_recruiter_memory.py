"""Recruiter long-term memory + message tombstone.

Two columns / tables:

  * ``recruiter_memory``                  -- per-recruiter K/V Pulse can read+write
  * ``recruiter_messages.tombstoned``     -- soft-delete for edit-and-resend

Revision ID: 0013_recruiter_memory
Revises: 0012_recruiter_chat
Create Date: 2026-05-10
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_recruiter_memory"
down_revision: str | Sequence[str] | None = "0012_recruiter_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recruiter_memory",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("actor_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False, server_default="self"),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("actor_hash", "scope", "key", name="uq_recruiter_memory_key"),
    )
    op.create_index("ix_recruiter_memory_actor", "recruiter_memory", ["actor_hash"])
    op.create_index("ix_recruiter_memory_actor_prefix", "recruiter_memory", ["actor_hash", "key"])

    op.add_column(
        "recruiter_messages",
        sa.Column("tombstoned", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index(
        "ix_recruiter_messages_active",
        "recruiter_messages",
        ["conversation_id", "tombstoned", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_recruiter_messages_active", table_name="recruiter_messages")
    op.drop_column("recruiter_messages", "tombstoned")
    op.drop_index("ix_recruiter_memory_actor_prefix", table_name="recruiter_memory")
    op.drop_index("ix_recruiter_memory_actor", table_name="recruiter_memory")
    op.drop_table("recruiter_memory")
