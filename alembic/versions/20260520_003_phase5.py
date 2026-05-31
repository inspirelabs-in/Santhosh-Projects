"""phase-5: events, crm webhooks, people, ledger, blocklist, prompts, shares, lighthouse, ad creatives, feedback reasons

Revision ID: 003
Revises: 002
Create Date: 2026-05-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- events bus -----------------------------------------------------------
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("topic", sa.String(64), nullable=False),
        sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="SET NULL"), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_attempts", sa.Integer, server_default="0", nullable=False),
        sa.Column("last_error", sa.Text, nullable=True),
    )
    op.create_index("ix_events_topic_created", "events", ["topic", "created_at"])
    op.create_index("ix_events_undelivered", "events", ["created_at"], postgresql_where=sa.text("delivered_at IS NULL"))

    # --- outbound webhooks ----------------------------------------------------
    op.create_table(
        "crm_webhooks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("topics", postgresql.JSONB, nullable=False),  # list[str]
        sa.Column("secret", sa.Text, nullable=False),  # HMAC signing key
        sa.Column("active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- person employment graph ---------------------------------------------
    op.create_table(
        "person_employment",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("person_name", sa.Text, nullable=False),
        sa.Column("linkedin_url", sa.Text, nullable=True),
        sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="SET NULL"), nullable=True),
        sa.Column("brand_name", sa.Text, nullable=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("started_at", sa.Date, nullable=True),
        sa.Column("ended_at", sa.Date, nullable=True),
        sa.Column("current", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_person_employment_brand_id", "person_employment", ["brand_id"])
    op.create_index("ix_person_employment_person", "person_employment", ["person_name"])
    op.create_index("ix_person_employment_linkedin", "person_employment", ["linkedin_url"])

    # --- scraping compliance ledger ------------------------------------------
    op.create_table(
        "scraping_ledger",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("domain", sa.Text, nullable=False),
        sa.Column("method", sa.String(8), server_default="GET", nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("robots_allowed", sa.Boolean, nullable=True),
        sa.Column("bytes_returned", sa.Integer, nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_scraping_ledger_domain", "scraping_ledger", ["domain"])
    op.create_index("ix_scraping_ledger_requested_at", "scraping_ledger", ["requested_at"])
    op.execute("CREATE INDEX ix_scraping_ledger_requested_brin ON scraping_ledger USING BRIN(requested_at)")

    # --- brand blocklist / allowlist -----------------------------------------
    op.create_table(
        "brand_blocklist",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False),  # 'block' | 'allow' | 'existing_client'
        sa.Column("pattern", sa.Text, nullable=False),  # exact domain OR glob OR brand-name (case-insensitive)
        sa.Column("scope", sa.String(16), server_default="domain", nullable=False),  # 'domain' | 'name'
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_brand_blocklist_pattern", "brand_blocklist", ["pattern"])
    op.create_index("ix_brand_blocklist_kind", "brand_blocklist", ["kind"])

    # --- prompt registry ------------------------------------------------------
    op.create_table(
        "prompts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_prompts_name_version"),
    )
    op.create_index("ix_prompts_name_active", "prompts", ["name"], postgresql_where=sa.text("active"))

    # --- reasoning trace shares ----------------------------------------------
    op.create_table(
        "reasoning_shares",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_traces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("revoked", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_reasoning_shares_trace", "reasoning_shares", ["trace_id"])

    # --- lighthouse audits ---------------------------------------------------
    op.create_table(
        "lighthouse_audits",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=True),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("strategy", sa.String(8), server_default="mobile", nullable=False),
        sa.Column("perf", sa.Numeric(5, 2), nullable=True),
        sa.Column("accessibility", sa.Numeric(5, 2), nullable=True),
        sa.Column("best_practices", sa.Numeric(5, 2), nullable=True),
        sa.Column("seo", sa.Numeric(5, 2), nullable=True),
        sa.Column("lcp_ms", sa.Integer, nullable=True),
        sa.Column("cls", sa.Numeric(6, 4), nullable=True),
        sa.Column("tbt_ms", sa.Integer, nullable=True),
        sa.Column("raw", postgresql.JSONB, nullable=True),
        sa.Column("audited_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_lighthouse_brand", "lighthouse_audits", ["brand_id"])

    # --- ad creatives --------------------------------------------------------
    op.create_table(
        "ad_creatives",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("brand_id", sa.BigInteger, sa.ForeignKey("brands.id", ondelete="SET NULL"), nullable=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("image_url", sa.Text, nullable=True),
        sa.Column("video_url", sa.Text, nullable=True),
        sa.Column("snapshot_url", sa.Text, nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("vision_analysis", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("platform", "external_id", name="uq_ad_creatives_platform_external"),
    )
    op.create_index("ix_ad_creatives_brand", "ad_creatives", ["brand_id"])

    # --- feedback reasons ----------------------------------------------------
    op.create_table(
        "feedback_reasons",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("feedback_id", sa.BigInteger, sa.ForeignKey("grabon_feedback.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_feedback_reasons_category", "feedback_reasons", ["category"])

    # --- domain whois cache --------------------------------------------------
    op.create_table(
        "domain_whois",
        sa.Column("domain", sa.Text, primary_key=True),
        sa.Column("created_on", sa.Date, nullable=True),
        sa.Column("registrar", sa.Text, nullable=True),
        sa.Column("expires_on", sa.Date, nullable=True),
        sa.Column("raw", postgresql.JSONB, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    for tbl in (
        "domain_whois",
        "feedback_reasons",
        "ad_creatives",
        "lighthouse_audits",
        "reasoning_shares",
        "prompts",
        "brand_blocklist",
        "scraping_ledger",
        "person_employment",
        "crm_webhooks",
        "events",
    ):
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
