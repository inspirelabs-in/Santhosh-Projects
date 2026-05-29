"""Make processed_messages.application_id nullable.

Skipped messages (no matching application) used a zero-UUID sentinel which
violates the FK constraint added in 0015. NULL is the correct representation.

Revision ID: 0017_nullable_pm_appid
Revises: 0016_widen_mail_source
Create Date: 2026-05-22
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0017_nullable_pm_appid"
down_revision: str | None = "0016_widen_mail_source"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.execute(
        f"UPDATE processed_messages SET application_id = NULL "
        f"WHERE application_id = '{_ZERO_UUID}'"
    )
    op.alter_column(
        "processed_messages",
        "application_id",
        existing_type=PG_UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE processed_messages SET application_id = '{_ZERO_UUID}' "
        f"WHERE application_id IS NULL"
    )
    op.alter_column(
        "processed_messages",
        "application_id",
        existing_type=PG_UUID(as_uuid=True),
        nullable=False,
    )
