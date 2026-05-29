"""agentic v2: voice_calls + assessment_results + meeting_sessions

Adds the persistence layer for the agentic hiring rounds:
  * voice_calls            -- outbound AI phone-screen calls
  * assessment_results     -- Predictive Index / Mettl / TestGorilla results
  * meeting_sessions       -- Teams technical + CEO meeting analyses

The pipeline-stage enum is a free-form VARCHAR (`applications.current_stage`)
so no enum-type migration is needed -- the new stage values are validated in
application code via ``models.v1.PipelineStage`` + ``ALLOWED_TRANSITIONS``.

Revision ID: 0005_agentic_v2_tables
Revises: 0004_role_problem_doc
Create Date: 2026-04-25
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_agentic_v2_tables"
down_revision: str | Sequence[str] | None = "0004_role_problem_doc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # voice_calls
    # ------------------------------------------------------------------
    op.create_table(
        "voice_calls",
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
        sa.Column("provider", sa.String(32), nullable=False, server_default="pipecat"),
        sa.Column("provider_call_id", sa.String(255), nullable=True),
        sa.Column("candidate_phone", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column("questions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evaluation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("overall_score", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.String(32), nullable=True),
        sa.Column("recording_r2_key", sa.String(500), nullable=True),
        sa.Column("transcript_r2_key", sa.String(500), nullable=True),
        sa.Column("emotion_features", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("callback_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("callback_reason", sa.String(500), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error", sa.Text(), nullable=True),
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
    op.create_index("ix_voice_calls_application_id", "voice_calls", ["application_id"])
    op.create_index("ix_voice_calls_provider_call_id", "voice_calls", ["provider_call_id"])
    op.create_index("ix_voice_calls_status", "voice_calls", ["status"])
    op.create_index("ix_voice_calls_scheduled_at", "voice_calls", ["scheduled_at"])
    op.create_index("ix_voice_calls_callback_at", "voice_calls", ["callback_at"])
    op.create_index("ix_voice_calls_app_status", "voice_calls", ["application_id", "status"])

    # ------------------------------------------------------------------
    # assessment_results
    # ------------------------------------------------------------------
    op.create_table(
        "assessment_results",
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
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("assessment_kind", sa.String(32), nullable=True),
        sa.Column("external_assessment_id", sa.String(255), nullable=True),
        sa.Column("invite_url", sa.String(1000), nullable=True),
        sa.Column("invite_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("normalized", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("normalized_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("percentile", sa.Numeric(5, 2), nullable=True),
        sa.Column("fit_band", sa.String(8), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="invited"),
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
        "ix_assessment_results_application_id", "assessment_results", ["application_id"]
    )
    op.create_index(
        "ix_assessment_results_external_id", "assessment_results", ["external_assessment_id"]
    )
    op.create_index("ix_assessment_results_status", "assessment_results", ["status"])
    op.create_index(
        "ix_assessment_results_completed_at", "assessment_results", ["completed_at"]
    )
    op.create_index(
        "ix_assessment_app_provider", "assessment_results", ["application_id", "provider"]
    )

    # ------------------------------------------------------------------
    # meeting_sessions
    # ------------------------------------------------------------------
    op.create_table(
        "meeting_sessions",
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
            "interview_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interviews.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("round", sa.String(16), nullable=False),
        sa.Column("teams_join_url", sa.String(1000), nullable=True),
        sa.Column("bot_provider", sa.String(32), nullable=False, server_default="recall"),
        sa.Column("bot_id", sa.String(255), nullable=True),
        sa.Column("bot_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column("recording_r2_key", sa.String(500), nullable=True),
        sa.Column("transcript_r2_key", sa.String(500), nullable=True),
        sa.Column("participants", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "candidate_emotion_timeline",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("technical_score", sa.Integer(), nullable=True),
        sa.Column("communication_score", sa.Integer(), nullable=True),
        sa.Column("confidence_score", sa.Integer(), nullable=True),
        sa.Column("overall_score", sa.Integer(), nullable=True),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("llm_report", sa.Text(), nullable=True),
        sa.Column("verdict", sa.String(32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
        "ix_meeting_sessions_application_id", "meeting_sessions", ["application_id"]
    )
    op.create_index("ix_meeting_sessions_interview_id", "meeting_sessions", ["interview_id"])
    op.create_index("ix_meeting_sessions_bot_id", "meeting_sessions", ["bot_id"])
    op.create_index("ix_meeting_sessions_bot_status", "meeting_sessions", ["bot_status"])
    op.create_index("ix_meeting_sessions_scheduled_at", "meeting_sessions", ["scheduled_at"])
    op.create_index(
        "ix_meeting_sessions_app_round", "meeting_sessions", ["application_id", "round"]
    )


def downgrade() -> None:
    op.drop_index("ix_meeting_sessions_app_round", table_name="meeting_sessions")
    op.drop_index("ix_meeting_sessions_scheduled_at", table_name="meeting_sessions")
    op.drop_index("ix_meeting_sessions_bot_status", table_name="meeting_sessions")
    op.drop_index("ix_meeting_sessions_bot_id", table_name="meeting_sessions")
    op.drop_index("ix_meeting_sessions_interview_id", table_name="meeting_sessions")
    op.drop_index("ix_meeting_sessions_application_id", table_name="meeting_sessions")
    op.drop_table("meeting_sessions")

    op.drop_index("ix_assessment_app_provider", table_name="assessment_results")
    op.drop_index("ix_assessment_results_completed_at", table_name="assessment_results")
    op.drop_index("ix_assessment_results_status", table_name="assessment_results")
    op.drop_index("ix_assessment_results_external_id", table_name="assessment_results")
    op.drop_index("ix_assessment_results_application_id", table_name="assessment_results")
    op.drop_table("assessment_results")

    op.drop_index("ix_voice_calls_app_status", table_name="voice_calls")
    op.drop_index("ix_voice_calls_callback_at", table_name="voice_calls")
    op.drop_index("ix_voice_calls_scheduled_at", table_name="voice_calls")
    op.drop_index("ix_voice_calls_status", table_name="voice_calls")
    op.drop_index("ix_voice_calls_provider_call_id", table_name="voice_calls")
    op.drop_index("ix_voice_calls_application_id", table_name="voice_calls")
    op.drop_table("voice_calls")
