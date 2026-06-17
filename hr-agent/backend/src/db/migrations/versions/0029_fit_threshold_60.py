"""Lower fit_green_threshold from 70 to 60 for binary tier system.

Resume-only scoring covers Skills + Experience only (CTC/logistics pending).
At 70 threshold, candidates with decent skills+experience get auto-rejected
before voice screen can verify CTC/logistics. 60 lets them through to voice.

Revision ID: 0029_fit_threshold_60
Revises: 0028_drop_pi_voice_only
Create Date: 2026-06-17
"""

from alembic import op

revision: str = "0029_fit_threshold_60"
down_revision: str = "0028_drop_pi_voice_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE policy_rules SET value = '60' "
        "WHERE key = 'fit_green_threshold' AND role_id IS NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE policy_rules SET value = '70' "
        "WHERE key = 'fit_green_threshold' AND role_id IS NULL"
    )
