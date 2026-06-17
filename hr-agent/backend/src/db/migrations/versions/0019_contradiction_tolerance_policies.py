"""Seed contradiction tolerance PolicyRules for Phase 2.

Revision ID: 0019_contradiction_tolerances
Revises: 0018_evidence_policy
Create Date: 2026-06-12
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_contradiction_tolerances"
down_revision: str = "0018_evidence_policy"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TOLERANCE_RULES = [
    {
        "key": "contradiction_tolerance.total_experience_years",
        "value": 1.0,
        "value_type": "float",
        "description": "Max allowed diff in experience years between stages before flagging",
    },
    {
        "key": "contradiction_tolerance.relevant_experience_years",
        "value": 1.0,
        "value_type": "float",
        "description": "Max allowed diff in relevant experience years between stages",
    },
    {
        "key": "contradiction_tolerance.current_ctc_lpa",
        "value": 2.0,
        "value_type": "float",
        "description": "Max allowed diff in current CTC (LPA) between stages",
    },
    {
        "key": "contradiction_tolerance.expected_ctc_lpa",
        "value": 2.0,
        "value_type": "float",
        "description": "Max allowed diff in expected CTC (LPA) between stages",
    },
    {
        "key": "contradiction_tolerance.notice_period_days",
        "value": 15,
        "value_type": "int",
        "description": "Max allowed diff in notice period days between stages",
    },
    {
        "key": "voice_backfill_on_contradiction",
        "value": True,
        "value_type": "bool",
        "description": "Whether to still backfill candidate profile from voice when a contradiction is detected",
    },
]


def upgrade() -> None:
    policy_rules = sa.table(
        "policy_rules",
        sa.column("id", sa.dialects.postgresql.UUID),
        sa.column("key", sa.String),
        sa.column("role_id", sa.dialects.postgresql.UUID),
        sa.column("value", sa.dialects.postgresql.JSONB),
        sa.column("value_type", sa.String),
        sa.column("description", sa.Text),
        sa.column("updated_by", sa.String),
        sa.column("version", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )

    for rule in TOLERANCE_RULES:
        op.execute(
            policy_rules.insert().values(
                id=sa.text("gen_random_uuid()"),
                key=rule["key"],
                role_id=None,
                value=sa.type_coerce(rule["value"], sa.dialects.postgresql.JSONB),
                value_type=rule["value_type"],
                description=rule["description"],
                updated_by="migration_0019",
                version=1,
                is_active=True,
            )
        )


def downgrade() -> None:
    keys = [r["key"] for r in TOLERANCE_RULES]
    op.execute(
        sa.text(
            "DELETE FROM policy_rules WHERE key = ANY(:keys) AND updated_by = 'migration_0019'"
        ).bindparams(keys=keys)
    )
