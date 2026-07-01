"""Reduce embedding dimension from 1536 to 256.

text-embedding-3-large supports variable dimensions via the ``dimensions``
param (OpenAI API). 256 is sufficient for talent-pool vector search and
drastically reduces storage + index size.

Steps:
  1. Drop the ivfflat index (depends on column type).
  2. Null out existing 1536-dim embeddings (incompatible with new type).
  3. ALTER COLUMN to Vector(256).
  4. Recreate the ivfflat index.

Revision ID: 0039_embedding_dim_256
Revises: 0038_role_pi_links
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0039_embedding_dim_256"
down_revision: str | Sequence[str] | None = "0038_role_pi_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop the old ivfflat index.
    op.execute("DROP INDEX IF EXISTS ix_candidate_profiles_embedding")

    # 2. Null out existing 1536-dim embeddings (can't cast across dims).
    op.execute("UPDATE candidate_profiles SET embedding = NULL WHERE embedding IS NOT NULL")

    # 3. Alter column type.
    op.alter_column("candidate_profiles", "embedding", type_=Vector(256))

    # 4. Recreate the index.
    op.execute(
        "CREATE INDEX ix_candidate_profiles_embedding "
        "ON candidate_profiles USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_candidate_profiles_embedding")
    op.execute("UPDATE candidate_profiles SET embedding = NULL WHERE embedding IS NOT NULL")
    op.alter_column("candidate_profiles", "embedding", type_=Vector(1536))
    op.execute(
        "CREATE INDEX ix_candidate_profiles_embedding "
        "ON candidate_profiles USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
