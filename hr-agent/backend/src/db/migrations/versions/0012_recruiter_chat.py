"""Recruiter-side chat: conversations + messages.

The candidate-facing chat lives in ``conversations`` / ``messages`` (one
conversation per application). The recruiter UI now also runs as a chat
agent (multiple conversations per recruiter, ChatGPT-style sidebar). We
keep them in separate tables so:

  * candidate convs are application-scoped + token-authed
  * recruiter convs are actor-scoped + dashboard-key-authed
  * deletion of an Application doesn't cascade-delete recruiter chats
    that mentioned it

Revision ID: 0012_recruiter_chat
Revises: 0011_role_screening_modality
Create Date: 2026-05-10
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_recruiter_chat"
down_revision: str | Sequence[str] | None = "0011_role_screening_modality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recruiter_conversations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Recruiter identity is the dashboard key prefix (admin_xxx /
        # rec_xxx / view_xxx). Stored hashed via SHA256 so a leaked DB
        # dump can't be replayed against the API.
        sa.Column("actor_hash", sa.String(64), nullable=False),
        sa.Column("actor_role", sa.String(16), nullable=False, server_default="recruiter"),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="false"),
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
    )
    op.create_index(
        "ix_recruiter_conversations_actor", "recruiter_conversations", ["actor_hash", "archived"]
    )
    op.create_index(
        "ix_recruiter_conversations_updated", "recruiter_conversations", ["updated_at"]
    )

    op.create_table(
        "recruiter_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recruiter_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),  # user | assistant | tool | system
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=True),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tool_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # Inline cards rendered by the chat UI (CandidateCard, RoleCard,
        # MetricCard, etc). Schema is open: ``{"kind": "candidate", ...}``.
        sa.Column("attachments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_recruiter_msgs_conv_seq"),
    )
    op.create_index(
        "ix_recruiter_messages_conv_seq", "recruiter_messages", ["conversation_id", "sequence"]
    )


def downgrade() -> None:
    op.drop_index("ix_recruiter_messages_conv_seq", table_name="recruiter_messages")
    op.drop_table("recruiter_messages")
    op.drop_index("ix_recruiter_conversations_updated", table_name="recruiter_conversations")
    op.drop_index("ix_recruiter_conversations_actor", table_name="recruiter_conversations")
    op.drop_table("recruiter_conversations")
