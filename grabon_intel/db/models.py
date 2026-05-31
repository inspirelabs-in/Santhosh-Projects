"""SQLAlchemy 2.0 ORM models for grabon-intel.

Phase-1 scope: signals, dossiers, brand_competitors, opportunities,
approvals, agent_traces, budget. Existing `brands`/`contacts` tables
from the legacy leadgen schema are referenced via FK but not redefined
here — Alembic migration only *adds* a `root_domain` generated column.
"""
from __future__ import annotations

import datetime as dt
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    brand_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("brands.id", ondelete="SET NULL"), nullable=True, index=True)
    brand_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_num: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(256), nullable=True, unique=True)
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_signals_brand_type_observed", "brand_id", "type", "observed_at"),
        Index("ix_signals_source_observed", "source", "observed_at"),
    )


class Dossier(Base):
    __tablename__ = "dossiers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("brands.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    cost_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("brand_id", "version", name="uq_dossiers_brand_version"),
        Index("ix_dossiers_brand_version", "brand_id", "version"),
    )


class BrandCompetitor(Base):
    __tablename__ = "brand_competitors"

    brand_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("brands.id", ondelete="CASCADE"), nullable=False)
    competitor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("brands.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0)
    discovered_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (PrimaryKeyConstraint("brand_id", "competitor_id", name="pk_brand_competitors"),)


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_value_inr: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), default="proposed", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    brand_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("brands.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    reviewer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_approvals_status_created", "status", "created_at"),)


class AgentTrace(Base):
    __tablename__ = "agent_traces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    brand_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("brands.id", ondelete="SET NULL"), nullable=True, index=True)
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    total_cost_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Budget(Base):
    """Daily spend ledger — atomic INCRBY via SQL `UPDATE ... RETURNING`."""

    __tablename__ = "budget"

    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    spent_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cap_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CollectorRun(Base):
    """Run metadata per collector pass. Lets us resume + audit."""

    __tablename__ = "collector_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collector: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signals_emitted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    signals_deduped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)


class Person(Base):
    """Decision-maker contacts linked to brands."""
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="vane_research")
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0.5)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_persons_brand_id", "brand_id"),
        UniqueConstraint("brand_id", "email", name="uq_persons_brand_email"),
    )


class Notification(Base):
    """In-app notification center."""
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    brand_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("brands.id", ondelete="SET NULL"), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_notifications_read_created", "read", "created_at"),
    )


class LeadEvent(Base):
    """Timeline events for lead lifecycle tracking."""
    __tablename__ = "lead_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_meta: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_lead_events_brand_created", "brand_id", "created_at"),
    )
