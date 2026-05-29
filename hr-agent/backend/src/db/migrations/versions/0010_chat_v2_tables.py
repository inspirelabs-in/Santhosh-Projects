"""Chat-first agentic V2 tables.

Replaces the V1 form-driven screening/assignment flow with a conversational
agent. Adds:

  * conversations         -- one per application; tracks current stage + state
  * messages              -- chat turns (user / assistant / tool / system)
  * screening_answers     -- structured extraction of the 6 screening fields
  * assignments           -- agent-generated brief + candidate submission

V1 columns on applications/screening_responses remain in place during the
cutover and are dropped in a follow-up migration once V2 is verified.

Revision ID: 0010_chat_v2_tables
Revises: 0009_reliability_tables
Create Date: 2026-05-10
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_chat_v2_tables"
down_revision: str | Sequence[str] | None = "0009_reliability_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # conversations
    # ------------------------------------------------------------------
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.String(32),
            nullable=False,
            server_default="intake",
        ),
        # Cached state machine + agent scratchpad (questions, candidate
        # facts learned, pending tool calls). Survives reconnects.
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # Pre-warmed artifacts so first-turn latency is minimal.
        sa.Column("prewarmed_questions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("prewarmed_assignment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.UniqueConstraint("application_id", name="uq_conversations_application_id"),
    )
    op.create_index("ix_conversations_application_id", "conversations", ["application_id"])
    op.create_index("ix_conversations_stage", "conversations", ["stage"])

    # ------------------------------------------------------------------
    # messages
    # ------------------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),  # system|user|assistant|tool
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=True),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tool_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_messages_conv_seq"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_conv_seq", "messages", ["conversation_id", "sequence"])

    # ------------------------------------------------------------------
    # screening_answers
    # ------------------------------------------------------------------
    op.create_table(
        "screening_answers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tailored_q1", sa.Text(), nullable=True),
        sa.Column("tailored_a1", sa.Text(), nullable=True),
        sa.Column("tailored_q2", sa.Text(), nullable=True),
        sa.Column("tailored_a2", sa.Text(), nullable=True),
        sa.Column("current_ctc_lpa", sa.Numeric(8, 2), nullable=True),
        sa.Column("expected_ctc_lpa", sa.Numeric(8, 2), nullable=True),
        sa.Column("notice_period_days", sa.Integer(), nullable=True),
        sa.Column("willing_to_relocate", sa.Boolean(), nullable=True),
        sa.Column("evaluation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("composite_score", sa.Integer(), nullable=True),
        sa.Column("knock_out_triggered", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("knock_out_reason", sa.String(255), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("application_id", name="uq_screening_answers_application_id"),
    )
    op.create_index("ix_screening_answers_application_id", "screening_answers", ["application_id"])

    # ------------------------------------------------------------------
    # assignments
    # ------------------------------------------------------------------
    op.create_table(
        "assignments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("brief_md", sa.Text(), nullable=True),
        sa.Column("problems", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("submission_format", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evaluation_rubric", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submission_url", sa.String(1000), nullable=True),
        sa.Column("submission_text", sa.Text(), nullable=True),
        sa.Column("submission_r2_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evaluation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
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
        sa.UniqueConstraint("application_id", name="uq_assignments_application_id"),
    )
    op.create_index("ix_assignments_application_id", "assignments", ["application_id"])


def downgrade() -> None:
    op.drop_index("ix_assignments_application_id", table_name="assignments")
    op.drop_table("assignments")

    op.drop_index("ix_screening_answers_application_id", table_name="screening_answers")
    op.drop_table("screening_answers")

    op.drop_index("ix_messages_conv_seq", table_name="messages")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_conversations_stage", table_name="conversations")
    op.drop_index("ix_conversations_application_id", table_name="conversations")
    op.drop_table("conversations")
