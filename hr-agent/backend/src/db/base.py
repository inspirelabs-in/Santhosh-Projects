"""SQLAlchemy declarative base. ORM tables mirror the schema in architecture.md.

These classes exist to give Alembic autogenerate-compatible metadata and to
back the repositories in src/db/repositories/. The Pydantic models in
src/models/ remain the wire/API source of truth.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[UUID] = _uuid_pk()
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    linkedin_url: Mapped[str | None] = mapped_column(String(500), index=True)
    status: Mapped[str] = mapped_column(String(50), default="intake", server_default="intake")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    consent_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_text_shown: Mapped[str | None] = mapped_column(Text)
    source_channel: Mapped[str | None] = mapped_column(String(50))
    temporal_workflow_id: Mapped[str | None] = mapped_column(String(255))
    timezone: Mapped[str | None] = mapped_column(String(64))

    applications: Mapped[list["Application"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    profiles: Mapped[list["CandidateProfileRow"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    consents: Mapped[list["ConsentArtifactRow"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    screening_questions: Mapped[list | dict] = mapped_column(JSONB, default=list)
    scoring_rubric: Mapped[dict] = mapped_column(JSONB, default=dict)
    cut_line: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    interviewer_panel: Mapped[list | dict] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="open", server_default="open")
    # role constraints (extensions beyond architecture.md minimal schema)
    ctc_min_lpa: Mapped[float | None] = mapped_column()
    ctc_max_lpa: Mapped[float | None] = mapped_column()
    max_notice_days: Mapped[int | None] = mapped_column(Integer)
    location: Mapped[str | None] = mapped_column(String(255))
    remote_policy: Mapped[str | None] = mapped_column(String(20))
    # V1: assignment fields (set by HR at role creation)
    assignment_brief: Mapped[str | None] = mapped_column(Text)
    assignment_instructions: Mapped[str | None] = mapped_column(Text)
    assignment_deadline_days: Mapped[int] = mapped_column(Integer, default=7, server_default="7")
    assignment_problem_doc_key: Mapped[str | None] = mapped_column(String(500))
    assignment_problem_filename: Mapped[str | None] = mapped_column(String(255))
    # Static Predictive Index Cognitive Assessment link. HR pastes this once
    # at role creation; backend emails it to candidates after voice screen
    # passes. NULL = skip the assessment courtesy email entirely.
    pi_cognitive_url: Mapped[str | None] = mapped_column(String(1000))
    # V2: screening modality picked by HR at role creation.
    #   "chat"  -> agentic chat-first screening (default)
    #   "voice" -> AI phone-screen (V1 voice path)
    screening_modality: Mapped[str] = mapped_column(
        String(16), default="chat", server_default="chat", index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[UUID] = _uuid_pk()
    candidate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        index=True,
    )
    role_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(50), default="active", server_default="active")
    fit_score: Mapped[int | None] = mapped_column(Integer)
    fit_tier: Mapped[str | None] = mapped_column(String(10))
    screening_score: Mapped[int | None] = mapped_column(Integer)
    # V1 pipeline JSONB columns
    screening_questions: Mapped[dict | list | None] = mapped_column(JSONB)
    screening_evaluation: Mapped[dict | None] = mapped_column(JSONB)
    assignment_submission: Mapped[dict | None] = mapped_column(JSONB)
    admin_review: Mapped[dict | None] = mapped_column(JSONB)
    journey_report: Mapped[str | None] = mapped_column(Text)
    current_stage: Mapped[str] = mapped_column(
        String(50), default="applied", server_default="applied", index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    candidate: Mapped[Candidate] = relationship(back_populates="applications")
    role: Mapped[Role | None] = relationship()


class CandidateProfileRow(Base):
    __tablename__ = "candidate_profiles"

    id: Mapped[UUID] = _uuid_pk()
    candidate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        index=True,
    )
    raw_resume_r2_key: Mapped[str | None] = mapped_column(String(500))
    parsed_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    extraction_confidence: Mapped[dict | None] = mapped_column(JSONB)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    candidate: Mapped[Candidate] = relationship(back_populates="profiles")


class ScreeningResponseRow(Base):
    __tablename__ = "screening_responses"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        index=True,
    )
    responses: Mapped[list | dict] = mapped_column(JSONB, nullable=False)
    knock_out_triggered: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    knock_out_reason: Mapped[str | None] = mapped_column(String(255))
    composite_score: Mapped[int | None] = mapped_column(Integer)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        index=True,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calendar_event_id: Mapped[str | None] = mapped_column(String(255))
    meeting_link: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="proposed", server_default="proposed")
    transcript_r2_key: Mapped[str | None] = mapped_column(String(500))
    report: Mapped[dict | None] = mapped_column(JSONB)
    feedback: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"

    message_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    application_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    mail_source: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    application_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB)
    model_version: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ConsentArtifactRow(Base):
    __tablename__ = "consent_artifacts"

    id: Mapped[UUID] = _uuid_pk()
    candidate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        index=True,
    )
    consent_type: Mapped[str] = mapped_column(String(50))
    consent_text: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(50))
    ip_address: Mapped[str | None] = mapped_column(INET)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    candidate: Mapped[Candidate] = relationship(back_populates="consents")


# ---------------------------------------------------------------------------
# Agentic V2: voice screening, assessment results, meeting sessions
# ---------------------------------------------------------------------------


class VoiceCall(Base):
    """Outbound AI phone-screen call. One per candidate per attempt.

    Lifecycle: pending -> dialing -> in_progress -> completed/failed/no_answer/
    callback_requested. Recording + transcript stored in R2; per-question scores
    + paralinguistic features land in JSONB after the post-call evaluator runs.
    """

    __tablename__ = "voice_calls"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), default="elevenlabs", server_default="elevenlabs")
    provider_call_id: Mapped[str | None] = mapped_column(String(255), index=True)
    candidate_phone: Mapped[str | None] = mapped_column(String(32))

    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending", index=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_sec: Mapped[float | None] = mapped_column(Float)

    # "screening" (5-Q phone screen) | "confirmation" (interview slot confirm).
    # Replaces the brittle ``not questions`` heuristic at the webhook layer.
    call_kind: Mapped[str] = mapped_column(
        String(20), default="screening", server_default="screening"
    )
    questions: Mapped[list | dict | None] = mapped_column(JSONB)
    answers: Mapped[list | dict | None] = mapped_column(JSONB)
    evaluation: Mapped[dict | None] = mapped_column(JSONB)
    overall_score: Mapped[int | None] = mapped_column(Integer)
    verdict: Mapped[str | None] = mapped_column(String(32))

    recording_r2_key: Mapped[str | None] = mapped_column(String(500))
    transcript_r2_key: Mapped[str | None] = mapped_column(String(500))
    emotion_features: Mapped[dict | None] = mapped_column(JSONB)

    # Reschedule support: when candidate says "call me later".
    callback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    callback_reason: Mapped[str | None] = mapped_column(String(500))
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_voice_calls_app_status", "application_id", "status"),
    )


class AssessmentResult(Base):
    """Result of a third-party (PI / Mettl / TestGorilla) or in-house assessment."""

    __tablename__ = "assessment_results"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "pi_cognitive"
    assessment_kind: Mapped[str | None] = mapped_column(String(32))  # behavioral | cognitive | tech
    external_assessment_id: Mapped[str | None] = mapped_column(String(255), index=True)
    invite_url: Mapped[str | None] = mapped_column(String(1000))

    invite_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    raw_result: Mapped[dict | None] = mapped_column(JSONB)
    normalized: Mapped[dict | None] = mapped_column(JSONB)
    normalized_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    percentile: Mapped[float | None] = mapped_column(Numeric(5, 2))
    fit_band: Mapped[str | None] = mapped_column(String(8))  # green | amber | red

    status: Mapped[str] = mapped_column(
        String(32), default="invited", server_default="invited", index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_assessment_app_provider", "application_id", "provider"),
    )


class MeetingSession(Base):
    """Technical or CEO Teams meeting that the agent observes.

    Created when HR books the meeting. Bot dispatcher fills recall_bot_id /
    started_at; analyzer fills transcript_r2_key + report.
    """

    __tablename__ = "meeting_sessions"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        index=True,
    )
    interview_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("interviews.id", ondelete="SET NULL"), index=True
    )
    round: Mapped[str] = mapped_column(String(16), nullable=False)  # technical | ceo
    teams_join_url: Mapped[str | None] = mapped_column(String(1000))

    bot_provider: Mapped[str] = mapped_column(
        String(32), default="recall", server_default="recall"
    )
    bot_id: Mapped[str | None] = mapped_column(String(255), index=True)
    bot_status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending", index=True
    )

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_sec: Mapped[float | None] = mapped_column(Float)

    recording_r2_key: Mapped[str | None] = mapped_column(String(500))
    transcript_r2_key: Mapped[str | None] = mapped_column(String(500))
    participants: Mapped[list | dict | None] = mapped_column(JSONB)

    candidate_emotion_timeline: Mapped[list | dict | None] = mapped_column(JSONB)
    technical_score: Mapped[int | None] = mapped_column(Integer)
    communication_score: Mapped[int | None] = mapped_column(Integer)
    confidence_score: Mapped[int | None] = mapped_column(Integer)
    overall_score: Mapped[int | None] = mapped_column(Integer)

    report: Mapped[dict | None] = mapped_column(JSONB)
    llm_report: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(String(32))

    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_meeting_sessions_app_round", "application_id", "round"),
    )


class PanelMember(Base):
    """Workspace-level interview panel member directory.

    HR sets these once at /settings/panels. Roles reference them by id when
    configuring per-round panels, so emails / availability / timezone don't
    need to be re-entered for every role.
    """

    __tablename__ = "panel_members"

    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    role_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # ^^ "technical" | "hr" | "ceo" | "founder" -- used to filter the picker.
    job_title: Mapped[str | None] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(
        String(64), default="Asia/Kolkata", server_default="Asia/Kolkata"
    )
    calendar_provider: Mapped[str] = mapped_column(
        String(20), default="microsoft", server_default="microsoft"
    )
    # ^^ "microsoft" | "google" | "none"
    calendar_id: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_panel_members_role_active", "role_type", "is_active"),
    )


class ConfigSetting(Base):
    """Runtime-editable configuration overlaid on env defaults.

    Managed via /admin/config endpoints. Secrets stored encrypted (see
    services/config_crypto.py); plain values stored as JSONB literals.
    """

    __tablename__ = "config_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB, nullable=False
    )
    is_secret: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)


class ConfigAuditRow(Base):
    __tablename__ = "config_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    old_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    new_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


# Webhook idempotency (Redis is primary, this is durable backup for audit trail)
class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[UUID] = _uuid_pk()
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_webhook_source_external"),
        Index("ix_webhook_events_source_external", "source", "external_id"),
    )


class EmailSend(Base):
    __tablename__ = "email_sends"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    candidate_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    to_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    template: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="sent")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class Conversation(Base):
    """Chat-first agentic V2: one conversation per application.

    Drives screening + assignment via LangGraph. Persistent so candidates
    can resume on reconnect. ``state`` is the agent scratchpad.
    """

    __tablename__ = "conversations"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        index=True,
    )
    stage: Mapped[str] = mapped_column(
        String(32), default="intake", server_default="intake", index=True
    )
    state: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    prewarmed_questions: Mapped[dict | list | None] = mapped_column(JSONB)
    prewarmed_assignment: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("application_id", name="uq_conversations_application_id"),
    )


class Message(Base):
    """One chat turn. Sequence is monotonically increasing per conversation."""

    __tablename__ = "messages"

    id: Mapped[UUID] = _uuid_pk()
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String(64))
    tool_calls: Mapped[list | dict | None] = mapped_column(JSONB)
    tool_result: Mapped[dict | list | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_messages_conv_seq"),
        Index("ix_messages_conv_seq", "conversation_id", "sequence"),
    )


class ScreeningAnswerRow(Base):
    """V2 screening: 2 tailored open-text + 4 fixed logistics fields.

    Replaces the V1 free-form ``screening_responses`` JSONB blob.
    """

    __tablename__ = "screening_answers"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        index=True,
    )
    tailored_q1: Mapped[str | None] = mapped_column(Text)
    tailored_a1: Mapped[str | None] = mapped_column(Text)
    tailored_q2: Mapped[str | None] = mapped_column(Text)
    tailored_a2: Mapped[str | None] = mapped_column(Text)
    current_ctc_lpa: Mapped[float | None] = mapped_column(Numeric(8, 2))
    expected_ctc_lpa: Mapped[float | None] = mapped_column(Numeric(8, 2))
    notice_period_days: Mapped[int | None] = mapped_column(Integer)
    willing_to_relocate: Mapped[bool | None] = mapped_column(Boolean)
    evaluation: Mapped[dict | None] = mapped_column(JSONB)
    composite_score: Mapped[int | None] = mapped_column(Integer)
    knock_out_triggered: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    knock_out_reason: Mapped[str | None] = mapped_column(String(255))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("application_id", name="uq_screening_answers_application_id"),
    )


class AssignmentRow(Base):
    """Agent-generated assignment brief + candidate submission."""

    __tablename__ = "assignments"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        index=True,
    )
    brief_md: Mapped[str | None] = mapped_column(Text)
    problems: Mapped[list | dict | None] = mapped_column(JSONB)
    submission_format: Mapped[dict | None] = mapped_column(JSONB)
    evaluation_rubric: Mapped[dict | None] = mapped_column(JSONB)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submission_url: Mapped[str | None] = mapped_column(String(1000))
    submission_text: Mapped[str | None] = mapped_column(Text)
    submission_r2_keys: Mapped[list | dict | None] = mapped_column(JSONB)
    evaluation: Mapped[dict | None] = mapped_column(JSONB)
    score: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("application_id", name="uq_assignments_application_id"),
    )


class RecruiterConversation(Base):
    """Recruiter-side chat conversation (ChatGPT-style sidebar entry).

    Multiple per recruiter. Dashboard-key-authed; ``actor_hash`` is the
    SHA256 of the dashboard key so a dump leak can't be replayed. ``state``
    is open scratchpad the agent uses across turns.
    """

    __tablename__ = "recruiter_conversations"

    id: Mapped[UUID] = _uuid_pk()
    actor_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_role: Mapped[str] = mapped_column(
        String(16), default="recruiter", server_default="recruiter"
    )
    title: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_recruiter_conversations_actor", "actor_hash", "archived"),
    )


class RecruiterMessage(Base):
    __tablename__ = "recruiter_messages"

    id: Mapped[UUID] = _uuid_pk()
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("recruiter_conversations.id", ondelete="CASCADE"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String(64))
    tool_calls: Mapped[list | dict | None] = mapped_column(JSONB)
    tool_result: Mapped[dict | list | None] = mapped_column(JSONB)
    attachments: Mapped[list | dict | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # Soft-delete for edit-and-resend; runner filters tombstoned rows when
    # rebuilding history. Permanent deletion done by archive job.
    tombstoned: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_recruiter_msgs_conv_seq"),
        Index("ix_recruiter_messages_conv_seq", "conversation_id", "sequence"),
        Index("ix_recruiter_messages_active", "conversation_id", "tombstoned", "sequence"),
    )


class RecruiterMemory(Base):
    """Per-recruiter long-term memory. ``key`` is dotted (e.g.
    ``preferences.default_screening_modality``); ``value`` is JSONB.
    """

    __tablename__ = "recruiter_memory"

    id: Mapped[UUID] = _uuid_pk()
    actor_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(32), default="self", server_default="self")
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("actor_hash", "scope", "key", name="uq_recruiter_memory_key"),
        Index("ix_recruiter_memory_actor_prefix", "actor_hash", "key"),
    )


class PipelineAlert(Base):
    __tablename__ = "pipeline_alerts"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(64))
    hours_stuck: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict | None] = mapped_column(JSONB)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
