"""Add evidence_records, decision_records, and policy_rules tables.

Phase 0 of the autonomous-agent architecture: provenance tracking for every
pipeline fact and decision, plus configurable policy rules replacing hardcoded
magic numbers.

Revision ID: 0018_evidence_policy
Revises: 0017_nullable_pm_appid
Create Date: 2026-06-12
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0018_evidence_policy"
down_revision: str = "0017_nullable_pm_appid"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_records",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("application_id", UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", UUID(as_uuid=True), nullable=False),
        sa.Column("fact_key", sa.String(128), nullable=False),
        sa.Column("fact_value", JSONB, nullable=False),
        sa.Column("source_stage", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("extraction_method", sa.String(32), nullable=False),
        sa.Column("evidence_text", sa.Text, nullable=True),
        sa.Column("source_ref", sa.String(500), nullable=True),
        sa.Column("char_offset_start", sa.Integer, nullable=True),
        sa.Column("char_offset_end", sa.Integer, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("langfuse_trace_id", sa.String(100), nullable=True),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("superseded_by_id", UUID(as_uuid=True), sa.ForeignKey("evidence_records.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_evidence_records_application_id", "evidence_records", ["application_id"])
    op.create_index("ix_evidence_records_candidate_id", "evidence_records", ["candidate_id"])
    op.create_index("ix_evidence_records_fact_key", "evidence_records", ["fact_key"])
    op.create_index("ix_evidence_app_fact", "evidence_records", ["application_id", "fact_key"])
    op.create_index("ix_evidence_candidate_fact", "evidence_records", ["candidate_id", "fact_key"])

    op.create_table(
        "decision_records",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("application_id", UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", UUID(as_uuid=True), nullable=False),
        sa.Column("decision_type", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("outcome_value", JSONB, nullable=True),
        sa.Column("evidence_ids", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("policy_rule_ids", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("audit_log_id", sa.BigInteger, nullable=True),
        sa.Column("langfuse_trace_id", sa.String(100), nullable=True),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_decision_records_application_id", "decision_records", ["application_id"])
    op.create_index("ix_decision_records_candidate_id", "decision_records", ["candidate_id"])
    op.create_index("ix_decision_records_decision_type", "decision_records", ["decision_type"])
    op.create_index("ix_decision_app_type", "decision_records", ["application_id", "decision_type"])

    op.create_table(
        "policy_rules",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("role_id", UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer, server_default=sa.text("1"), nullable=False),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("key", "role_id", name="uq_policy_rules_key_role"),
    )
    op.create_index("ix_policy_rules_key", "policy_rules", ["key"])
    op.create_index("ix_policy_rules_role_id", "policy_rules", ["role_id"])
    op.create_index("ix_policy_rules_key_role", "policy_rules", ["key", "role_id"])

    # Seed global default policy rules matching current hardcoded values
    op.execute(
        """
        INSERT INTO policy_rules (id, key, role_id, value, value_type, description, updated_by) VALUES
        (gen_random_uuid(), 'fit_green_threshold', NULL, '70', 'int', 'Fit score >= this = GREEN tier', 'migration_0018'),
        (gen_random_uuid(), 'fit_amber_threshold', NULL, '50', 'int', 'Fit score >= this = AMBER tier', 'migration_0018'),
        (gen_random_uuid(), 'ctc_overshoot_multiplier', NULL, '1.15', 'float', 'Expected CTC > role max * this = knockout', 'migration_0018'),
        (gen_random_uuid(), 'scoring_weights.skills', NULL, '50', 'int', 'Fit score weight for skills dimension', 'migration_0018'),
        (gen_random_uuid(), 'scoring_weights.experience', NULL, '25', 'int', 'Fit score weight for experience dimension', 'migration_0018'),
        (gen_random_uuid(), 'scoring_weights.ctc', NULL, '15', 'int', 'Fit score weight for CTC dimension', 'migration_0018'),
        (gen_random_uuid(), 'scoring_weights.logistics', NULL, '10', 'int', 'Fit score weight for logistics dimension', 'migration_0018'),
        (gen_random_uuid(), 'classification_hr_review_confidence', NULL, '0.7', 'float', 'Below this confidence, email classification goes to HR review', 'migration_0018'),
        (gen_random_uuid(), 'screening_knock_score', NULL, '50', 'int', 'Score assigned when screening knockout triggered', 'migration_0018'),
        (gen_random_uuid(), 'screening_pass_score', NULL, '75', 'int', 'Score assigned when screening passes without knockout', 'migration_0018'),
        (gen_random_uuid(), 'voice_screen_blank_char_threshold', NULL, '80', 'int', 'Transcript shorter than this = blank call', 'migration_0018'),
        (gen_random_uuid(), 'voice_screen_blank_ratio_threshold', NULL, '0.5', 'float', 'Less than this fraction of Qs answered = blank call', 'migration_0018'),
        (gen_random_uuid(), 'stall_screening_no_response_hours', NULL, '72', 'int', 'Hours before screening no-response triggers stall alert', 'migration_0018'),
        (gen_random_uuid(), 'stall_assignment_no_submission_hours', NULL, '120', 'int', 'Hours before assignment no-submission triggers stall alert', 'migration_0018'),
        (gen_random_uuid(), 'stall_tech_review_hours', NULL, '48', 'int', 'Hours before tech review pending triggers stall alert', 'migration_0018'),
        (gen_random_uuid(), 'stall_hr_review_hours', NULL, '48', 'int', 'Hours before HR review pending triggers stall alert', 'migration_0018'),
        (gen_random_uuid(), 'stall_interview_not_confirmed_hours', NULL, '24', 'int', 'Hours before unconfirmed interview triggers stall alert', 'migration_0018'),
        (gen_random_uuid(), 'stall_ceo_approval_hours', NULL, '72', 'int', 'Hours before CEO approval pending triggers stall alert', 'migration_0018'),
        (gen_random_uuid(), 'stall_voice_screen_stuck_hours', NULL, '4', 'int', 'Hours before stuck voice screen triggers stall alert', 'migration_0018'),
        (gen_random_uuid(), 'stall_assessment_no_result_hours', NULL, '168', 'int', 'Hours before assessment no-result triggers stall alert', 'migration_0018'),
        (gen_random_uuid(), 'fit_no_profile_default_score', NULL, '55', 'int', 'Default fit score when no candidate profile exists', 'migration_0018'),
        (gen_random_uuid(), 'screening_grey_zone_width', NULL, '10', 'int', 'Width of grey zone around screening cut line', 'migration_0018')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("policy_rules")
    op.drop_table("decision_records")
    op.drop_table("evidence_records")
