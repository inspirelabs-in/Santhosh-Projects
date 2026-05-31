"""embedding dim 1024 -> 384 (fastembed BAAI/bge-small-en-v1.5)

Revision ID: 004
Revises: 003
Create Date: 2026-05-21

Idempotent column-type swap. We DROP the HNSW index first (pgvector
won't let us alter the column while an index references it), shrink the
column to vector(384), and rebuild the index. Existing rows are nulled —
the embedder will repopulate on next dossier write.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_dossiers_embedding_hnsw")
    op.execute("UPDATE dossiers SET embedding = NULL")
    op.execute("ALTER TABLE dossiers ALTER COLUMN embedding TYPE vector(384)")
    op.execute(
        "CREATE INDEX ix_dossiers_embedding_hnsw ON dossiers USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_dossiers_embedding_hnsw")
    op.execute("UPDATE dossiers SET embedding = NULL")
    op.execute("ALTER TABLE dossiers ALTER COLUMN embedding TYPE vector(1024)")
    op.execute(
        "CREATE INDEX ix_dossiers_embedding_hnsw ON dossiers USING hnsw (embedding vector_cosine_ops)"
    )
