"""Fix missing/incorrect foreign key constraints and add case-insensitive email index.

Changes:
  * processed_messages.application_id — add FK → applications.id ON DELETE CASCADE
  * applications.role_id — change FK to ON DELETE SET NULL
  * candidates.email — add unique index on LOWER(email) for case-insensitive dedup

Revision ID: 0015_fk_fixes_and_pipeline_alert
Revises: 0014_voice_call_kind
Create Date: 2026-05-21
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0015_fk_fixes_and_pipeline_alert"
down_revision: str | None = "0014_voice_call_kind"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # 1. Clean orphaned processed_messages before adding FK
    op.execute(
        "DELETE FROM processed_messages "
        "WHERE application_id IS NOT NULL "
        "AND application_id NOT IN (SELECT id FROM applications)"
    )
    op.create_foreign_key(
        "fk_processed_messages_application_id",
        "processed_messages",
        "applications",
        ["application_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 2. applications.role_id — drop old FK, create new with ON DELETE SET NULL
    #    Convention: Alembic-generated FK name is "applications_role_id_fkey"
    op.drop_constraint("applications_role_id_fkey", "applications", type_="foreignkey")
    op.create_foreign_key(
        "applications_role_id_fkey",
        "applications",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. Case-insensitive unique index on candidate email
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_candidates_email_lower "
        "ON candidates (LOWER(email)) WHERE email IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_candidates_email_lower")

    op.drop_constraint("applications_role_id_fkey", "applications", type_="foreignkey")
    op.create_foreign_key(
        "applications_role_id_fkey",
        "applications",
        "roles",
        ["role_id"],
        ["id"],
    )

    op.drop_constraint(
        "fk_processed_messages_application_id",
        "processed_messages",
        type_="foreignkey",
    )
