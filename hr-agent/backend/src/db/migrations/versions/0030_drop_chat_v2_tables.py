"""Drop Chat V2 tables: conversations, messages, screening_answers.

Chat V2 (LangGraph) was decommissioned in migration 0028 which forced all
roles to screening_modality='voice'. These tables are safe to drop.

Revision ID: 0030_drop_chat_v2_tables
Revises: 0029_fit_threshold_60
Create Date: 2026-06-17
"""

from alembic import op

revision: str = "0030_drop_chat_v2_tables"
down_revision: str = "0029_fit_threshold_60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("messages")
    op.drop_table("screening_answers")
    op.drop_table("conversations")


def downgrade() -> None:
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

    op.create_table(
        "conversations",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("application_id", PG_UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("stage", sa.String(32), server_default="intake", nullable=False),
        sa.Column("state", JSONB, server_default="{}"),
        sa.Column("prewarmed_questions", JSONB),
        sa.Column("prewarmed_assignment", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("application_id", name="uq_conversations_application_id"),
    )
    op.create_table(
        "messages",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", PG_UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text),
        sa.Column("tool_name", sa.String(64)),
        sa.Column("tool_calls", JSONB),
        sa.Column("tool_result", JSONB),
        sa.Column("model", sa.String(64)),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_messages_conv_seq"),
    )
    op.create_table(
        "screening_answers",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("application_id", PG_UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("tailored_q1", sa.Text),
        sa.Column("tailored_a1", sa.Text),
        sa.Column("tailored_q2", sa.Text),
        sa.Column("tailored_a2", sa.Text),
        sa.Column("current_ctc_lpa", sa.Numeric(8, 2)),
        sa.Column("expected_ctc_lpa", sa.Numeric(8, 2)),
        sa.Column("notice_period_days", sa.Integer),
        sa.Column("willing_to_relocate", sa.Boolean),
        sa.Column("evaluation", JSONB),
        sa.Column("composite_score", sa.Integer),
        sa.Column("knock_out_triggered", sa.Boolean, server_default="false"),
        sa.Column("knock_out_reason", sa.String(255)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("application_id", name="uq_screening_answers_application_id"),
    )
