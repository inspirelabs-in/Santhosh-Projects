"""role problem-statement document

Adds R2 key + filename for the Word doc HR attaches at role creation. This
doc is linked from the assignment invite email so candidates can download
the problem statement.

Revision ID: 0004_role_problem_doc
Revises: 0003_v1_screening_assignment
Create Date: 2026-04-23
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_role_problem_doc"
down_revision: str | Sequence[str] | None = "0003_v1_screening_assignment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("roles", sa.Column("assignment_problem_doc_key", sa.String(500), nullable=True))
    op.add_column("roles", sa.Column("assignment_problem_filename", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("roles", "assignment_problem_filename")
    op.drop_column("roles", "assignment_problem_doc_key")
