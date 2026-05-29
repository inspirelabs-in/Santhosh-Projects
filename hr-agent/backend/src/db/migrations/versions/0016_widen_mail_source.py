"""Widen processed_messages.mail_source from VARCHAR(32) to VARCHAR(128).

Values like 'imap_gmail:skipped_subject_mismatch' exceed 32 chars.

Revision ID: 0016_widen_mail_source
Revises: 0015_fk_fixes_and_pipeline_alert
Create Date: 2026-05-21
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_widen_mail_source"
down_revision: str | None = "0015_fk_fixes_and_pipeline_alert"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "processed_messages",
        "mail_source",
        type_=sa.String(128),
        existing_type=sa.String(32),
    )


def downgrade() -> None:
    op.alter_column(
        "processed_messages",
        "mail_source",
        type_=sa.String(32),
        existing_type=sa.String(128),
    )
