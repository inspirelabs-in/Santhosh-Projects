"""Add outreach_sequences table and monitoring support.

Revision ID: 005
Revises: 004
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outreach_sequences",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("to_email", sa.Text, nullable=False),
        sa.Column("subjects", JSONB, nullable=False),
        sa.Column("bodies", JSONB, nullable=False),
        sa.Column("html_bodies", JSONB, nullable=True),
        sa.Column("schedule", JSONB, nullable=False),
        sa.Column("current_step", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("next_send_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sequences_status_next", "outreach_sequences", ["status", "next_send_at"])

    # LLM response cache table
    op.create_table(
        "llm_cache",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("cache_key", sa.String(128), nullable=False, unique=True),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("response", sa.Text, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_cents", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("llm_cache")
    op.drop_index("ix_sequences_status_next", table_name="outreach_sequences")
    op.drop_table("outreach_sequences")
