"""initial grabon-intel schema

Revision ID: 001
Revises:
Create Date: 2026-05-18

Adds: signals, dossiers, brand_competitors, opportunities, approvals,
agent_traces, budget, collector_runs. Adds root_domain generated column
to existing brands table. Enables pgvector + (optional) timescaledb.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return insp.has_table(name)


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()

    # Extensions — pgvector required, timescaledb optional.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    timescale_ok = False
    try:
        bind.execute(sa.text("SAVEPOINT try_timescale"))
        bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        bind.execute(sa.text("RELEASE SAVEPOINT try_timescale"))
        timescale_ok = True
    except Exception:
        bind.execute(sa.text("ROLLBACK TO SAVEPOINT try_timescale"))
        timescale_ok = False

    # If legacy brands table missing, create a minimal stub so FKs resolve.
    if not _has_table("brands"):
        op.create_table(
            "brands",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("name", sa.Text, nullable=False),
            sa.Column("domain", sa.Text, nullable=True),
            sa.Column("status", sa.String(32), server_default="new", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_brands_domain", "brands", ["domain"])
        op.create_index("ix_brands_status", "brands", ["status"])

    # root_domain generated column on brands (idempotent).
    if not _has_column("brands", "root_domain"):
        op.execute(
            "ALTER TABLE brands ADD COLUMN root_domain TEXT "
            "GENERATED ALWAYS AS (lower(regexp_replace(coalesce(domain,''), '^www\\.', ''))) STORED"
        )
        op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_brands_root_domain ON brands(root_domain) WHERE root_domain <> ''")

    # signals
    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="SET NULL"), nullable=True),
        sa.Column("brand_hint", sa.Text, nullable=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("value_num", sa.Numeric, nullable=True),
        sa.Column("value_text", sa.Text, nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(256), nullable=True, unique=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_signals_brand_id", "signals", ["brand_id"])
    op.create_index("ix_signals_brand_type_observed", "signals", ["brand_id", "type", "observed_at"])
    op.create_index("ix_signals_source_observed", "signals", ["source", "observed_at"])
    # BRIN on observed_at for time-range scans (cheap, always useful).
    op.execute("CREATE INDEX ix_signals_observed_brin ON signals USING BRIN (observed_at)")

    if timescale_ok:
        # Convert to hypertable. Safe to call after rows exist; here it's empty.
        op.execute(
            "SELECT create_hypertable('signals', 'observed_at', "
            "chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE, migrate_data => TRUE)"
        )

    # dossiers
    op.create_table(
        "dossiers",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("data", postgresql.JSONB, nullable=False),
        sa.Column("markdown", sa.Text, nullable=True),
        # pgvector column — created via raw SQL to keep alembic deps minimal.
        sa.Column("cost_cents", sa.Integer, server_default="0", nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("brand_id", "version", name="uq_dossiers_brand_version"),
    )
    op.execute("ALTER TABLE dossiers ADD COLUMN embedding vector(384)")
    op.create_index("ix_dossiers_brand_version", "dossiers", ["brand_id", "version"])
    op.execute("CREATE INDEX ix_dossiers_embedding_hnsw ON dossiers USING hnsw (embedding vector_cosine_ops)")

    # brand_competitors
    op.create_table(
        "brand_competitors",
        sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("competitor_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), server_default="0", nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("brand_id", "competitor_id", name="pk_brand_competitors"),
    )

    # opportunities
    op.create_table(
        "opportunities",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("estimated_impact", sa.Text, nullable=True),
        sa.Column("estimated_value_inr", sa.BigInteger, nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), server_default="0", nullable=False),
        sa.Column("status", sa.String(32), server_default="proposed", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_opportunities_brand_id", "opportunities", ["brand_id"])

    # approvals
    op.create_table(
        "approvals",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.BigInteger, nullable=False),
        sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("reviewer", sa.String(128), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("diff", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_approvals_brand_id", "approvals", ["brand_id"])
    op.create_index("ix_approvals_status_created", "approvals", ["status", "created_at"])

    # agent_traces
    op.create_table(
        "agent_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workflow_id", sa.String(128), nullable=False),
        sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("steps", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("total_cost_cents", sa.Integer, server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("status", sa.String(16), server_default="ok", nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_traces_workflow_id", "agent_traces", ["workflow_id"])
    op.create_index("ix_agent_traces_brand_id", "agent_traces", ["brand_id"])
    # pgcrypto for gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # budget (atomic daily ledger)
    op.create_table(
        "budget",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("spent_cents", sa.Integer, server_default="0", nullable=False),
        sa.Column("cap_cents", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # collector_runs
    op.create_table(
        "collector_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("collector", sa.String(64), nullable=False),
        sa.Column("params", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signals_emitted", sa.Integer, server_default="0", nullable=False),
        sa.Column("signals_deduped", sa.Integer, server_default="0", nullable=False),
        sa.Column("status", sa.String(16), server_default="running", nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("cursor", sa.Text, nullable=True),
    )
    op.create_index("ix_collector_runs_collector", "collector_runs", ["collector"])


def downgrade() -> None:
    op.drop_table("collector_runs")
    op.drop_table("budget")
    op.drop_index("ix_agent_traces_brand_id", table_name="agent_traces")
    op.drop_index("ix_agent_traces_workflow_id", table_name="agent_traces")
    op.drop_table("agent_traces")
    op.drop_index("ix_approvals_status_created", table_name="approvals")
    op.drop_index("ix_approvals_brand_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_opportunities_brand_id", table_name="opportunities")
    op.drop_table("opportunities")
    op.drop_table("brand_competitors")
    op.execute("DROP INDEX IF EXISTS ix_dossiers_embedding_hnsw")
    op.drop_index("ix_dossiers_brand_version", table_name="dossiers")
    op.drop_table("dossiers")
    op.execute("DROP INDEX IF EXISTS ix_signals_observed_brin")
    op.drop_index("ix_signals_source_observed", table_name="signals")
    op.drop_index("ix_signals_brand_type_observed", table_name="signals")
    op.drop_index("ix_signals_brand_id", table_name="signals")
    op.drop_table("signals")
    op.execute("DROP INDEX IF EXISTS uq_brands_root_domain")
    op.execute("ALTER TABLE brands DROP COLUMN IF EXISTS root_domain")
