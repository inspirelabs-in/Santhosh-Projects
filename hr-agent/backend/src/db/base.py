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
    org_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
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
    org_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    screening_questions: Mapped[list | dict] = mapped_column(JSONB, default=list)
    scoring_rubric: Mapped[dict] = mapped_column(JSONB, default=dict)
    cut_line: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    interviewer_panel: Mapped[list | dict] = mapped_column(JSONB, default=list)
    # Column default is "open", but the create/apply paths set "draft" explicitly
    # when the role has an assignment stage and no published assignment PDF yet —
    # such a role is held at "draft" (hidden from the careers list) until a PDF is
    # uploaded or drafted via generate_assignment_for_role.
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
    # Screening modality: voice is the only supported channel.
    screening_modality: Mapped[str] = mapped_column(
        String(16), default="voice", server_default="voice", index=True
    )
    pipeline_template: Mapped[list | None] = mapped_column(JSONB)
    # Revamp: persona-derived, role-specific scoring criteria (EvaluationSpec).
    # Generated at JD time, user-approved, consumed by every scoring stage.
    # Legacy pipeline_template/scoring_rubric kept for back-compat; the relational
    # role_pipeline_stages rows are the source of truth going forward.
    evaluation_spec: Mapped[dict | None] = mapped_column(JSONB)
    # Role-tuned company context (models.context.RoleContext), generated at JD
    # time, intensity scaled to the role; grounds every downstream LLM stage.
    company_context: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pipeline_stages: Mapped[list["RolePipelineStage"]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
        order_by="RolePipelineStage.position",
    )


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
    org_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), index=True
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
    # Revamp: position in the role's configurable pipeline. ``current_stage_key``
    # references role_pipeline_stages.stage_key; ``stage_status`` is the
    # within-stage lifecycle (StageStatus). Legacy ``current_stage`` stays
    # authoritative until the runtime cut-over, so nothing breaks.
    current_stage_key: Mapped[str | None] = mapped_column(String(64), index=True)
    stage_status: Mapped[str | None] = mapped_column(String(20))
    # Per-stage outcome overlay (the generic stage-runner). Map keyed by
    # role_pipeline_stages.stage_key -> {processing_status, verdict, result_ref,
    # updated_at}. "Processed vs not" is DERIVED from current_stage_key + the role
    # template (position); this stores ONLY what position can't express: the
    # per-stage verdict (pending|on_going|pass|fail), the processing claim
    # (idempotency), and a pointer to the result. Absent stage == unprocessed.
    stage_results: Mapped[dict | None] = mapped_column(JSONB)
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
    # Idempotency guard for ingesting/evaluating the call RESULT (separate from
    # ``status`` which is the call lifecycle). See models.v1.ProcessingStatus.
    processing_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    campaign_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("voice_campaigns.id", ondelete="SET NULL"),
        index=True,
    )

    __table_args__ = (
        Index("ix_voice_calls_app_status", "application_id", "status"),
    )


class VoiceCampaign(Base):
    """Bulk outbound voice call campaign. Tracks dispatch of 1-N calls."""

    __tablename__ = "voice_campaigns"

    id: Mapped[UUID] = _uuid_pk()
    role_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="SET NULL"), index=True
    )
    call_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="draft", server_default="draft", index=True
    )
    total_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completed_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_concurrent: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    dispatch_rate_per_minute: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    target_application_ids: Mapped[list | None] = mapped_column(JSONB)
    context_template: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssessmentResult(Base):
    """Result of a third-party (Mettl / TestGorilla) or in-house assessment."""

    __tablename__ = "assessment_results"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "mettl", "testgorilla"
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
    round: Mapped[str] = mapped_column(String(16), nullable=False)  # technical | ceo (legacy)
    # Revamp: references the role_pipeline_stages.stage_key this meeting belongs
    # to, so multiple interview rounds (technical_1, technical_2, ...) are
    # distinguishable. ``round`` kept for back-compat.
    stage_key: Mapped[str | None] = mapped_column(String(64), index=True)
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

    negotiation_state: Mapped[dict | None] = mapped_column(JSONB)

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
    org_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
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
    expertise_tags: Mapped[list | None] = mapped_column(JSONB)
    department: Mapped[str | None] = mapped_column(String(100))
    max_interviews_per_week: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    seniority_level: Mapped[str | None] = mapped_column(String(20))  # junior | mid | senior | lead | executive
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
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


# ---------------------------------------------------------------------------
# Evidence & Policy layer (Phase 0 of autonomous-agent architecture)
# ---------------------------------------------------------------------------


class EvidenceRecord(Base):
    """Provenance-tracked fact extracted from any pipeline stage.

    Each row is one atomic fact (e.g. "expected_ctc_lpa = 18") with full
    source lineage: which stage, what document, how extracted, how confident.
    Cross-stage contradiction detection queries (application_id, fact_key).
    """

    __tablename__ = "evidence_records"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        index=True,
    )
    candidate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)

    fact_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    fact_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB, nullable=False
    )

    source_stage: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False)

    evidence_text: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(String(500))
    char_offset_start: Mapped[int | None] = mapped_column(Integer)
    char_offset_end: Mapped[int | None] = mapped_column(Integer)

    confidence: Mapped[float | None] = mapped_column(Float)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(100))
    model_version: Mapped[str | None] = mapped_column(String(100))

    superseded_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("evidence_records.id", ondelete="SET NULL"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_evidence_app_fact", "application_id", "fact_key"),
        Index("ix_evidence_candidate_fact", "candidate_id", "fact_key"),
    )


class DecisionRecord(Base):
    """Auditable agent decision with citation graph.

    Links to the EvidenceRecord rows and PolicyRule rows that informed the
    decision. Also links to the existing AuditLog row for backward compat.
    """

    __tablename__ = "decision_records"

    id: Mapped[UUID] = _uuid_pk()
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        index=True,
    )
    candidate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)

    decision_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome_value: Mapped[dict | None] = mapped_column(JSONB)

    evidence_ids: Mapped[list] = mapped_column(JSONB, default=list)
    policy_rule_ids: Mapped[list] = mapped_column(JSONB, default=list)

    audit_log_id: Mapped[int | None] = mapped_column(BigInteger)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(100))
    model_version: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(50))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_decision_app_type", "application_id", "decision_type"),
    )


class PolicyRule(Base):
    """Configurable pipeline threshold / weight / rule.

    NULL role_id = global default. Non-null = per-role override.
    resolve_policy(key, role_id) checks per-role first, falls back to global.
    """

    __tablename__ = "policy_rules"

    id: Mapped[UUID] = _uuid_pk()
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    role_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        index=True,
    )

    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB, nullable=False
    )
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("key", "role_id", name="uq_policy_rules_key_role"),
        Index("ix_policy_rules_key_role", "key", "role_id"),
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


# ---------------------------------------------------------------------------
# Phase 3: Supervisor event bus + action log
# ---------------------------------------------------------------------------


class SupervisorEvent(Base):
    """Typed, durable event that the supervisor engine processes.

    Events are claimed by a single worker (status-based locking) and
    processed exactly once. dedup_key prevents duplicate firing.
    """
    __tablename__ = "supervisor_events"
    __table_args__ = (
        Index("ix_supervisor_events_status_created", "status", "created_at"),
        Index("ix_supervisor_events_app", "application_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    application_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    candidate_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    dedup_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )  # pending → claimed → processed | failed
    claimed_by: Mapped[str | None] = mapped_column(String(128))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SupervisorAction(Base):
    """Proposed or executed action from the supervisor engine.

    In shadow mode, actions are written with executed=False for HR review.
    In execute mode, executed=True after successful tool invocation.
    """
    __tablename__ = "supervisor_actions"
    __table_args__ = (
        Index("ix_supervisor_actions_event", "event_id"),
        Index("ix_supervisor_actions_app", "application_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("supervisor_events.id"), nullable=False
    )
    application_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    candidate_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    action_params: Mapped[dict] = mapped_column(JSONB, default=dict)
    reasoning: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # shadow | execute
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    execution_result: Mapped[dict | None] = mapped_column(JSONB)
    evidence_ids: Mapped[list | None] = mapped_column(JSONB)
    policy_rule_ids: Mapped[list | None] = mapped_column(JSONB)
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_by: Mapped[str | None] = mapped_column(String(128))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_record_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Phase 0 (revamp): org/tenancy, identity, configurable pipelines, work backbone
#
# All additive. Existing tables keep their columns; new columns are nullable.
# The supervisor_* tables above are superseded by domain_events below and will
# be retired in a later contract migration (not dropped here).
# ---------------------------------------------------------------------------


class Organization(Base):
    """Tenant anchor. One row today (GrabOn); tenant-ready for multi-org later.

    ``hiring_persona`` holds the org's worldview (models.persona.HiringPersona)
    that seeds per-role evaluation specs. Static but editable in Settings.
    """

    __tablename__ = "organizations"

    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    hiring_persona: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    """Identity + seat. Schema only this pass — auth wiring (Firebase) is
    deferred and the existing dashboard-key auth is untouched. Adding this table
    breaks nothing; it is simply unused until SP1.
    """

    __tablename__ = "users"

    id: Mapped[UUID] = _uuid_pk()
    org_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    google_sub: Mapped[str | None] = mapped_column(String(255), index=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    role: Mapped[str] = mapped_column(String(16), default="member", server_default="member")
    seat_status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RolePipelineStage(Base):
    """One stage in a role's configurable pipeline. Ordered by ``position``.

    Relational shell + JSONB content: the row gives queryable structure (type,
    order, mode, enabled), while ``config`` and ``eval_spec`` carry the flexible
    per-stage bits (see models/pipeline.py). Remove a stage = delete/disable the
    row; add a second interview = insert another row with stage_type='interview'.
    """

    __tablename__ = "role_pipeline_stages"

    id: Mapped[UUID] = _uuid_pk()
    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    stage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    eval_spec: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    role: Mapped["Role"] = relationship(back_populates="pipeline_stages")

    __table_args__ = (
        UniqueConstraint("role_id", "stage_key", name="uq_role_pipeline_stage_key"),
        Index("ix_role_pipeline_stages_role_pos", "role_id", "position"),
    )


class DomainEvent(Base):
    """Durable, typed event log — the work backbone. Replaces the never-working
    supervisor engine and the ephemeral Redis-only events; Redis stays as the
    realtime push only.

    The derived inbox reads the actionable subset:
    ``requires_action = true AND resolved_at IS NULL``.
    """

    __tablename__ = "domain_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    application_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    role_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    actor: Mapped[str] = mapped_column(String(255), default="system", server_default="system")
    requires_action: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        Index("ix_domain_events_inbox", "org_id", "requires_action", "resolved_at"),
        Index("ix_domain_events_app_created", "application_id", "created_at"),
    )


class ActionOverlay(Base):
    """Mutable human state over *derived* inbox items (snooze / assign / dismiss).

    The inbox itself is derived (a query over domain_events + application state),
    so this table only holds what derivation can't: the human overlay. Keyed by a
    stable ``action_key`` (e.g. 'app:{id}:assessment_review' or 'event:{id}').
    """

    __tablename__ = "action_overlay"

    id: Mapped[UUID] = _uuid_pk()
    org_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    action_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_to: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Notification(Base):
    """FYI feed (the 🔔 bell). Informational counterpart to action items;
    read/unread, no resolution. Derived from informational domain_events.
    """

    __tablename__ = "notifications"

    id: Mapped[UUID] = _uuid_pk()
    org_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(500))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        Index("ix_notifications_org_read", "org_id", "read_at"),
        Index("ix_notifications_user_read", "user_id", "read_at"),
    )


class RecruiterArtifact(Base):
    """Structured, editable artifact the agent produces in a conversation.

    Replaces the inline "confirm-card" block for drafts: the agent writes a typed
    JSON document here (e.g. a ``role_draft``); the frontend opens it in a side
    panel as an editable form. Both human edits and agent edits update ``content``
    (bumping ``version``); ``apply`` turns it into the real entity.
    """

    __tablename__ = "recruiter_artifacts"

    id: Mapped[UUID] = _uuid_pk()
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("recruiter_conversations.id", ondelete="CASCADE"),
        index=True,
    )
    org_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    application_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    title: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_recruiter_artifacts_conv_status", "conversation_id", "status"),
    )
