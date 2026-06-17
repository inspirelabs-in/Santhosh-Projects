"""Seed PolicyRules for Phase 6: confidence-driven gate removal.

Revision ID: 0021_confidence_gate_policies
Revises: 0020_supervisor
Create Date: 2026-06-12
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_confidence_gate_policies"
down_revision: str = "0020_supervisor"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

CONFIDENCE_RULES = [
    {
        "key": "confidence_gate_threshold",
        "value": 0.85,
        "value_type": "float",
        "description": "Minimum pipeline confidence score to auto-advance past a non-finals human gate",
    },
    {
        "key": "confidence_gate_threshold.screening",
        "value": 0.85,
        "value_type": "float",
        "description": "Confidence threshold for auto-advancing past screening HR review",
    },
    {
        "key": "confidence_gate_threshold.voice_screen",
        "value": 0.85,
        "value_type": "float",
        "description": "Confidence threshold for auto-advancing past voice screening HR review",
    },
    {
        "key": "confidence_gate_threshold.technical",
        "value": 0.90,
        "value_type": "float",
        "description": "Confidence threshold for auto-advancing past technical round HR review (higher bar)",
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

    for rule in CONFIDENCE_RULES:
        op.execute(
            policy_rules.insert().values(
                id=sa.text("gen_random_uuid()"),
                key=rule["key"],
                role_id=None,
                value=sa.type_coerce(rule["value"], sa.dialects.postgresql.JSONB),
                value_type=rule["value_type"],
                description=rule["description"],
                updated_by="migration_0021",
                version=1,
                is_active=True,
            )
        )


def downgrade() -> None:
    keys = [r["key"] for r in CONFIDENCE_RULES]
    op.execute(
        sa.text(
            "DELETE FROM policy_rules WHERE key = ANY(:keys) AND updated_by = 'migration_0021'"
        ).bindparams(keys=keys)
    )
