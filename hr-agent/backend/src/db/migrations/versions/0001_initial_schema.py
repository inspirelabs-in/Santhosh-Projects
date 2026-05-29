"""initial schema

Creates extensions (vector, uuid-ossp, pg_trgm) and all core tables from
references/architecture.md: candidates, roles, applications, candidate_profiles,
screening_responses, interviews, audit_log, consent_artifacts, webhook_events.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-04-20
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Extensions
    # ------------------------------------------------------------------
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ------------------------------------------------------------------
    # candidates
    # ------------------------------------------------------------------
    op.create_table(
        "candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), unique=True),
        sa.Column("phone", sa.String(20)),
        sa.Column("name", sa.String(255)),
        sa.Column("linkedin_url", sa.String(500)),
        sa.Column("status", sa.String(50), nullable=False, server_default="intake"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("consent_captured_at", sa.DateTime(timezone=True)),
        sa.Column("consent_text_shown", sa.Text),
        sa.Column("source_channel", sa.String(50)),
        sa.Column("temporal_workflow_id", sa.String(255)),
    )
    op.create_index("ix_candidates_phone", "candidates", ["phone"])
    op.create_index("ix_candidates_linkedin_url", "candidates", ["linkedin_url"])
    op.create_index("ix_candidates_status", "candidates", ["status"])

    # ------------------------------------------------------------------
    # roles
    # ------------------------------------------------------------------
    op.create_table(
        "roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("jd_text", sa.Text, nullable=False),
        sa.Column("screening_questions", JSONB, nullable=False, server_default="[]"),
        sa.Column("scoring_rubric", JSONB, nullable=False, server_default="{}"),
        sa.Column("cut_line", sa.Integer, nullable=False, server_default="60"),
        sa.Column("interviewer_panel", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("ctc_min_lpa", sa.Float),
        sa.Column("ctc_max_lpa", sa.Float),
        sa.Column("max_notice_days", sa.Integer),
        sa.Column("location", sa.String(255)),
        sa.Column("remote_policy", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_roles_status", "roles", ["status"])

    # ------------------------------------------------------------------
    # applications
    # ------------------------------------------------------------------
    op.create_table(
        "applications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role_id", UUID(as_uuid=True), sa.ForeignKey("roles.id")),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("fit_score", sa.Integer),
        sa.Column("fit_tier", sa.String(10)),
        sa.Column("screening_score", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_applications_candidate_id", "applications", ["candidate_id"])
    op.create_index("ix_applications_role_id", "applications", ["role_id"])
    op.create_index("ix_applications_status", "applications", ["status"])

    # ------------------------------------------------------------------
    # candidate_profiles
    # ------------------------------------------------------------------
    op.create_table(
        "candidate_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("raw_resume_r2_key", sa.String(500)),
        sa.Column("parsed_data", JSONB, nullable=False),
        sa.Column("extraction_confidence", JSONB),
        sa.Column("embedding", Vector(1536)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_candidate_profiles_candidate_id", "candidate_profiles", ["candidate_id"])
    # Approximate-nearest-neighbour index for semantic talent-pool search.
    op.execute(
        "CREATE INDEX ix_candidate_profiles_embedding "
        "ON candidate_profiles USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    # ------------------------------------------------------------------
    # screening_responses
    # ------------------------------------------------------------------
    op.create_table(
        "screening_responses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "application_id",
            UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("responses", JSONB, nullable=False),
        sa.Column("knock_out_triggered", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("knock_out_reason", sa.String(255)),
        sa.Column("composite_score", sa.Integer),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_screening_responses_application_id", "screening_responses", ["application_id"])

    # ------------------------------------------------------------------
    # interviews
    # ------------------------------------------------------------------
    op.create_table(
        "interviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "application_id",
            UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("calendar_event_id", sa.String(255)),
        sa.Column("meeting_link", sa.String(500)),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("transcript_r2_key", sa.String(500)),
        sa.Column("report", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_interviews_application_id", "interviews", ["application_id"])
    op.create_index("ix_interviews_scheduled_at", "interviews", ["scheduled_at"])

    # ------------------------------------------------------------------
    # audit_log -- append-only
    # ------------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("candidate_id", UUID(as_uuid=True)),
        sa.Column("application_id", UUID(as_uuid=True)),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("details", JSONB),
        sa.Column("model_version", sa.String(100)),
        sa.Column("prompt_version", sa.String(50)),
        sa.Column("langfuse_trace_id", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_log_candidate_id", "audit_log", ["candidate_id"])
    op.create_index("ix_audit_log_application_id", "audit_log", ["application_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    # Enforce append-only at the DB layer.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_no_update_delete() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER audit_log_immutable "
        "BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_no_update_delete()"
    )

    # ------------------------------------------------------------------
    # consent_artifacts
    # ------------------------------------------------------------------
    op.create_table(
        "consent_artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consent_type", sa.String(50)),
        sa.Column("consent_text", sa.Text, nullable=False),
        sa.Column("channel", sa.String(50)),
        sa.Column("ip_address", INET),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_consent_artifacts_candidate_id", "consent_artifacts", ["candidate_id"])

    # ------------------------------------------------------------------
    # webhook_events -- durable idempotency log (Redis is primary fast cache)
    # ------------------------------------------------------------------
    op.create_table(
        "webhook_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source", "external_id", name="uq_webhook_source_external"),
    )
    op.create_index("ix_webhook_events_source_external", "webhook_events", ["source", "external_id"])


def downgrade() -> None:
    op.drop_table("webhook_events")
    op.drop_table("consent_artifacts")
    op.execute("DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_no_update_delete()")
    op.drop_table("audit_log")
    op.drop_table("interviews")
    op.drop_table("screening_responses")
    op.execute("DROP INDEX IF EXISTS ix_candidate_profiles_embedding")
    op.drop_table("candidate_profiles")
    op.drop_table("applications")
    op.drop_table("roles")
    op.drop_table("candidates")
    # Extensions left in place -- they're shared with other databases.
