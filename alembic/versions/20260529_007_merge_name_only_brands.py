"""Merge name-only brands into their domain-bearing twins.

Revision ID: 007
Revises: 006
"""
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Re-point signals from the name-only duplicate to the domain-bearing twin
    op.execute("""
        UPDATE signals SET brand_id = twin.id
        FROM brands dup
        JOIN brands twin ON LOWER(twin.name) = LOWER(dup.name) AND twin.domain <> ''
        WHERE dup.domain = ''
          AND signals.brand_id = dup.id
          AND dup.id <> twin.id
    """)

    # Re-point dossiers
    op.execute("""
        UPDATE dossiers SET brand_id = twin.id
        FROM brands dup
        JOIN brands twin ON LOWER(twin.name) = LOWER(dup.name) AND twin.domain <> ''
        WHERE dup.domain = ''
          AND dossiers.brand_id = dup.id
          AND dup.id <> twin.id
    """)

    # Re-point approvals
    op.execute("""
        UPDATE approvals SET brand_id = twin.id
        FROM brands dup
        JOIN brands twin ON LOWER(twin.name) = LOWER(dup.name) AND twin.domain <> ''
        WHERE dup.domain = ''
          AND approvals.brand_id = dup.id
          AND dup.id <> twin.id
    """)

    # Re-point agent_traces
    op.execute("""
        UPDATE agent_traces SET brand_id = twin.id
        FROM brands dup
        JOIN brands twin ON LOWER(twin.name) = LOWER(dup.name) AND twin.domain <> ''
        WHERE dup.domain = ''
          AND agent_traces.brand_id = dup.id
          AND dup.id <> twin.id
    """)

    # Delete the now-orphaned name-only duplicates
    op.execute("""
        DELETE FROM brands
        WHERE domain = ''
          AND EXISTS (
              SELECT 1 FROM brands twin
              WHERE LOWER(twin.name) = LOWER(brands.name)
                AND twin.domain <> ''
                AND twin.id <> brands.id
          )
    """)


def downgrade() -> None:
    pass
