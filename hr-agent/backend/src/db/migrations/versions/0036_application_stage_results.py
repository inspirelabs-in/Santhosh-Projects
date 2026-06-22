"""Add applications.stage_results: per-stage outcome overlay for the stage-runner.

The generic stage-runner (fix/10) walks the role's configured pipeline
(``role_pipeline_stages``) and records each candidate's per-stage outcome here.

"Processed vs not" is derived from ``current_stage_key`` + the template (position),
so this column stores ONLY what position can't express:
``{ "<stage_key>": {"processing_status": ..., "verdict": ..., "result_ref": ...,
"updated_at": ...} }``. Absent stage == unprocessed/pending. Additive / nullable /
non-breaking.

Revision ID: 0036_application_stage_results
Revises: 0035_org_email_domains_seed
Create Date: 2026-06-22
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0036_application_stage_results"
down_revision: str | Sequence[str] | None = "0035_org_email_domains_seed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("stage_results", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "stage_results")
