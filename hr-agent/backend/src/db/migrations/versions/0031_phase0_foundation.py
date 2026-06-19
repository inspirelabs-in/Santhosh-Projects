"""Phase 0 revamp foundation: org/tenancy, identity, configurable pipelines,
persona-driven evaluation, and the durable work backbone.

ALL ADDITIVE / NON-BREAKING:
  - new tables: organizations, users, role_pipeline_stages, domain_events,
    action_overlay, notifications
  - new nullable columns: *.org_id, roles.evaluation_spec,
    applications.current_stage_key + stage_status, meeting_sessions.stage_key
  - legacy columns (current_stage, round, pipeline_template, scoring_rubric) are
    left untouched and stay authoritative until the runtime cut-over
  - backfill: one GrabOn org row, org_id stamped everywhere, a default pipeline
    seeded per existing role, best-effort current_stage_key mapping
  - evidence_records.candidate_id FK added NOT VALID (won't fail on legacy data)
  - no auth/OAuth changes; the users table is created but unused this pass

Revision ID: 0031_phase0_foundation
Revises: 0030_drop_chat_v2_tables
Create Date: 2026-06-19
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

revision: str = "0031_phase0_foundation"
down_revision: str | Sequence[str] | None = "0030_drop_chat_v2_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Default pipeline seeded onto existing roles. Mirrors models/pipeline.py
# DEFAULT_PIPELINE (kept inline so the migration is self-contained). Automation
# line: auto through assignment-send, manual from assessment_review onward.
_DEFAULT_PIPELINE = [
    ("intake", "intake", "Intake", "auto"),
    ("parse", "parse", "Resume Parse", "auto"),
    ("fit", "fit", "Fit Score", "auto"),
    ("screening", "screening", "Resume Screening", "auto"),
    ("voice_screen", "voice_screen", "Voice Screen", "auto"),
    ("assignment", "assignment", "Assignment", "auto"),
    ("assessment_review", "assessment_review", "Assessment Review", "manual"),
    ("technical", "interview", "Technical Interview", "manual"),
    ("ceo", "interview", "CEO Interview", "manual"),
    ("hr", "interview", "HR Interview", "manual"),
    ("decision", "decision", "Decision", "manual"),
    ("offer", "offer", "Offer", "manual"),
]


def upgrade() -> None:
    _ts = sa.text("now()")

    # --- new tables -------------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("hiring_persona", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("settings", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_ts),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_ts),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"])

    op.create_table(
        "users",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", PG_UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE")),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("google_sub", sa.String(255)),
        sa.Column("avatar_url", sa.String(500)),
        sa.Column("role", sa.String(16), server_default="member"),
        sa.Column("seat_status", sa.String(16), server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_ts),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_org_id", "users", ["org_id"])
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_google_sub", "users", ["google_sub"])

    op.create_table(
        "role_pipeline_stages",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("role_id", PG_UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", PG_UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="SET NULL")),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("stage_type", sa.String(32), nullable=False),
        sa.Column("stage_key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("mode", sa.String(16), server_default="manual"),
        sa.Column("is_enabled", sa.Boolean, server_default="true"),
        sa.Column("config", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("eval_spec", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_ts),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_ts),
        sa.UniqueConstraint("role_id", "stage_key", name="uq_role_pipeline_stage_key"),
    )
    op.create_index("ix_role_pipeline_stages_role_id", "role_pipeline_stages", ["role_id"])
    op.create_index("ix_role_pipeline_stages_org_id", "role_pipeline_stages", ["org_id"])
    op.create_index("ix_role_pipeline_stages_role_pos", "role_pipeline_stages", ["role_id", "position"])

    op.create_table(
        "domain_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("org_id", PG_UUID(as_uuid=True)),
        sa.Column("application_id", PG_UUID(as_uuid=True)),
        sa.Column("role_id", PG_UUID(as_uuid=True)),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("actor", sa.String(255), server_default="system"),
        sa.Column("requires_action", sa.Boolean, server_default="false"),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_ts),
    )
    op.create_index("ix_domain_events_org_id", "domain_events", ["org_id"])
    op.create_index("ix_domain_events_application_id", "domain_events", ["application_id"])
    op.create_index("ix_domain_events_type", "domain_events", ["type"])
    op.create_index("ix_domain_events_created_at", "domain_events", ["created_at"])
    op.create_index("ix_domain_events_inbox", "domain_events", ["org_id", "requires_action", "resolved_at"])
    op.create_index("ix_domain_events_app_created", "domain_events", ["application_id", "created_at"])

    op.create_table(
        "action_overlay",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", PG_UUID(as_uuid=True)),
        sa.Column("action_key", sa.String(160), nullable=False),
        sa.Column("snoozed_until", sa.DateTime(timezone=True)),
        sa.Column("assigned_to", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("dismissed_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_ts),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_ts),
        sa.UniqueConstraint("action_key", name="uq_action_overlay_key"),
    )
    op.create_index("ix_action_overlay_org_id", "action_overlay", ["org_id"])
    op.create_index("ix_action_overlay_key", "action_overlay", ["action_key"])

    op.create_table(
        "notifications",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", PG_UUID(as_uuid=True)),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("type", sa.String(48), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text),
        sa.Column("link", sa.String(500)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_ts),
    )
    op.create_index("ix_notifications_org_id", "notifications", ["org_id"])
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_index("ix_notifications_org_read", "notifications", ["org_id", "read_at"])
    op.create_index("ix_notifications_user_read", "notifications", ["user_id", "read_at"])

    # --- new columns on existing tables (all nullable) --------------------
    op.add_column("candidates", sa.Column("org_id", PG_UUID(as_uuid=True)))
    op.add_column("roles", sa.Column("org_id", PG_UUID(as_uuid=True)))
    op.add_column("roles", sa.Column("evaluation_spec", JSONB))
    op.add_column("applications", sa.Column("org_id", PG_UUID(as_uuid=True)))
    op.add_column("applications", sa.Column("current_stage_key", sa.String(64)))
    op.add_column("applications", sa.Column("stage_status", sa.String(20)))
    op.add_column("panel_members", sa.Column("org_id", PG_UUID(as_uuid=True)))
    op.add_column("meeting_sessions", sa.Column("stage_key", sa.String(64)))

    op.create_foreign_key("fk_candidates_org", "candidates", "organizations", ["org_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_roles_org", "roles", "organizations", ["org_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_applications_org", "applications", "organizations", ["org_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_panel_members_org", "panel_members", "organizations", ["org_id"], ["id"], ondelete="SET NULL")

    op.create_index("ix_candidates_org_id", "candidates", ["org_id"])
    op.create_index("ix_roles_org_id", "roles", ["org_id"])
    op.create_index("ix_applications_org_id", "applications", ["org_id"])
    op.create_index("ix_applications_current_stage_key", "applications", ["current_stage_key"])
    op.create_index("ix_panel_members_org_id", "panel_members", ["org_id"])
    op.create_index("ix_meeting_sessions_stage_key", "meeting_sessions", ["stage_key"])

    # Partial unique index: at most one active meeting per (application, stage).
    # Scoped to stage_key IS NOT NULL so legacy NULL rows never collide.
    op.create_index(
        "uq_meeting_sessions_active_stage",
        "meeting_sessions",
        ["application_id", "stage_key"],
        unique=True,
        postgresql_where=sa.text(
            "stage_key IS NOT NULL AND bot_status IN ('pending','scheduled','in_call')"
        ),
    )

    # evidence_records.candidate_id FK — NOT VALID so it never fails on legacy
    # rows; enforces referential integrity for all new rows.
    op.execute(
        "ALTER TABLE evidence_records "
        "ADD CONSTRAINT fk_evidence_candidate "
        "FOREIGN KEY (candidate_id) REFERENCES candidates(id) ON DELETE CASCADE NOT VALID"
    )

    # --- backfill ---------------------------------------------------------
    conn = op.get_bind()
    org_id = conn.execute(
        sa.text(
            "INSERT INTO organizations (name, slug, hiring_persona, settings) "
            "VALUES ('GrabOn', 'grabon', '{}'::jsonb, '{}'::jsonb) RETURNING id"
        )
    ).scalar()

    for table in ("candidates", "roles", "applications", "panel_members"):
        conn.execute(
            sa.text(f"UPDATE {table} SET org_id = :org WHERE org_id IS NULL"),
            {"org": org_id},
        )

    # Seed the default pipeline for every existing role.
    role_ids = [r[0] for r in conn.execute(sa.text("SELECT id FROM roles")).fetchall()]
    for rid in role_ids:
        for position, (stage_key, stage_type, label, mode) in enumerate(_DEFAULT_PIPELINE):
            conn.execute(
                sa.text(
                    "INSERT INTO role_pipeline_stages "
                    "(role_id, org_id, position, stage_type, stage_key, label, mode) "
                    "VALUES (:role_id, :org, :pos, :stype, :skey, :label, :mode)"
                ),
                {
                    "role_id": rid,
                    "org": org_id,
                    "pos": position,
                    "stype": stage_type,
                    "skey": stage_key,
                    "label": label,
                    "mode": mode,
                },
            )

    # Best-effort map of legacy current_stage -> (current_stage_key, stage_status).
    # current_stage stays authoritative; this only seeds the new columns.
    conn.execute(
        sa.text(
            """
            UPDATE applications SET
              current_stage_key = CASE
                WHEN current_stage = 'applied' THEN 'intake'
                WHEN current_stage IN ('screening_sent','screening_submitted','screening_evaluated') THEN 'screening'
                WHEN current_stage = 'needs_hr_review' THEN 'assessment_review'
                WHEN current_stage IN ('assignment_sent','assignment_submitted','assessment_invited') THEN 'assignment'
                WHEN current_stage IN ('report_ready','assessment_completed','assessment_pending_review','assessment_evaluated') THEN 'assessment_review'
                WHEN current_stage LIKE 'voice_screen%' THEN 'voice_screen'
                WHEN current_stage LIKE 'technical%' THEN 'technical'
                WHEN current_stage LIKE 'ceo%' THEN 'ceo'
                WHEN current_stage LIKE 'hr_%' THEN 'hr'
                WHEN current_stage = 'hired' THEN 'offer'
                WHEN current_stage = 'rejected' THEN 'decision'
                ELSE NULL
              END,
              stage_status = CASE
                WHEN current_stage = 'needs_hr_review' THEN 'parked'
                WHEN current_stage = 'hired' THEN 'passed'
                WHEN current_stage = 'rejected' THEN 'failed'
                WHEN current_stage LIKE '%_in_progress' THEN 'in_progress'
                WHEN current_stage LIKE '%_scheduled' THEN 'scheduled'
                WHEN current_stage = 'voice_screen_callback_requested' THEN 'scheduled'
                WHEN current_stage LIKE '%_submitted' OR current_stage LIKE '%_completed'
                     OR current_stage LIKE '%_evaluated' OR current_stage LIKE '%_pending_approval'
                     OR current_stage = 'report_ready' THEN 'completed'
                ELSE 'active'
              END
            """
        )
    )


def downgrade() -> None:
    op.execute("ALTER TABLE evidence_records DROP CONSTRAINT IF EXISTS fk_evidence_candidate")

    op.drop_index("uq_meeting_sessions_active_stage", table_name="meeting_sessions")
    op.drop_index("ix_meeting_sessions_stage_key", table_name="meeting_sessions")
    op.drop_index("ix_panel_members_org_id", table_name="panel_members")
    op.drop_index("ix_applications_current_stage_key", table_name="applications")
    op.drop_index("ix_applications_org_id", table_name="applications")
    op.drop_index("ix_roles_org_id", table_name="roles")
    op.drop_index("ix_candidates_org_id", table_name="candidates")

    op.drop_constraint("fk_panel_members_org", "panel_members", type_="foreignkey")
    op.drop_constraint("fk_applications_org", "applications", type_="foreignkey")
    op.drop_constraint("fk_roles_org", "roles", type_="foreignkey")
    op.drop_constraint("fk_candidates_org", "candidates", type_="foreignkey")

    op.drop_column("meeting_sessions", "stage_key")
    op.drop_column("panel_members", "org_id")
    op.drop_column("applications", "stage_status")
    op.drop_column("applications", "current_stage_key")
    op.drop_column("applications", "org_id")
    op.drop_column("roles", "evaluation_spec")
    op.drop_column("roles", "org_id")
    op.drop_column("candidates", "org_id")

    op.drop_table("notifications")
    op.drop_table("action_overlay")
    op.drop_table("domain_events")
    op.drop_table("role_pipeline_stages")
    op.drop_table("users")
    op.drop_table("organizations")
