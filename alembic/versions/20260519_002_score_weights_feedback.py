"""score_weights table + feedback enrichment

Revision ID: 002
Revises: 001
Create Date: 2026-05-19

Adds:
  - score_weights: versioned coefficient store written by the retrain workflow.
  - feedback table (creates if missing — legacy schema may already have it
    in a different shape, we keep our own to stay self-contained).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    op.create_table(
        "score_weights",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),  # 'meeting_prob' | 'close_value' | 'rubric'
        sa.Column("coefficients", postgresql.JSONB, nullable=False),
        sa.Column("metrics", postgresql.JSONB, nullable=True),
        sa.Column("training_rows", sa.Integer, server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_score_weights_kind_version", "score_weights", ["kind", "version"], unique=True)

    if not _has_table("grabon_feedback"):
        op.create_table(
            "grabon_feedback",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
            sa.Column("label", sa.String(32), nullable=False),
            # labels: good_fit | bad_fit | meeting_booked | closed_won | closed_lost
            sa.Column("note", sa.Text, nullable=True),
            sa.Column("value_inr", sa.BigInteger, nullable=True),
            sa.Column("reviewer", sa.String(128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_grabon_feedback_brand_id", "grabon_feedback", ["brand_id"])
        op.create_index("ix_grabon_feedback_label", "grabon_feedback", ["label"])


def downgrade() -> None:
    if _has_table("grabon_feedback"):
        op.drop_index("ix_grabon_feedback_label", table_name="grabon_feedback")
        op.drop_index("ix_grabon_feedback_brand_id", table_name="grabon_feedback")
        op.drop_table("grabon_feedback")
    op.drop_index("ix_score_weights_kind_version", table_name="score_weights")
    op.drop_table("score_weights")
