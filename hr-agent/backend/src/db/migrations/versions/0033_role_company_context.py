"""Add roles.company_context: role-tuned grounding context generated at JD time.

Distinct from org.hiring_persona (static, org-wide). Intensity scales with the
role; fed to every downstream LLM stage. Additive / non-breaking.

Revision ID: 0033_role_company_context
Revises: 0032_recruiter_artifacts
Create Date: 2026-06-20
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0033_role_company_context"
down_revision: str | Sequence[str] | None = "0032_recruiter_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("roles", sa.Column("company_context", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("roles", "company_context")
