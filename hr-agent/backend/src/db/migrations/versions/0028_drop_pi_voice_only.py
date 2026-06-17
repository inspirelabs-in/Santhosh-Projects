"""Drop pi_cognitive_url column and default screening_modality to voice.

Revision ID: 0028_drop_pi_voice_only
Revises: 0027_assignment_deadline
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0028_drop_pi_voice_only"
down_revision: str = "0027_assignment_deadline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = [c["name"] for c in sa.inspect(conn).get_columns("roles")]

    if "pi_cognitive_url" in cols:
        op.drop_column("roles", "pi_cognitive_url")

    # Set all existing roles to voice screening (chat is no longer supported)
    op.execute("UPDATE roles SET screening_modality = 'voice' WHERE screening_modality != 'voice'")

    # Update server default from 'chat' to 'voice'
    op.alter_column(
        "roles",
        "screening_modality",
        server_default="voice",
    )


def downgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("pi_cognitive_url", sa.String(length=1000), nullable=True),
    )
    op.alter_column(
        "roles",
        "screening_modality",
        server_default="chat",
    )
