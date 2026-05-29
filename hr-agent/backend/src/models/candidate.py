"""Candidate, application, role, and intake schemas.

These mirror the Postgres schema in references/architecture.md and are the
single source of truth for API payloads, DB rows, and LLM inputs.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# ---------------------------------------------------------------------------
# Enums (mirror VARCHAR columns in Postgres)
# ---------------------------------------------------------------------------


class SourceChannel(StrEnum):
    FORM = "form"
    EMAIL = "email"
    REFERRAL = "referral"
    LINKEDIN = "linkedin"
    NAUKRI = "naukri"


class CandidateStatus(StrEnum):
    INTAKE = "intake"
    CLASSIFIED = "classified"
    PARSED = "parsed"
    SCORED = "scored"
    SCREENING_SENT = "screening_sent"
    SCREENING_COMPLETED = "screening_completed"
    SHORTLISTED = "shortlisted"
    SCHEDULING = "scheduling"
    SCHEDULED = "scheduled"
    INTERVIEWED = "interviewed"
    OFFERED = "offered"
    REJECTED = "rejected"
    COLD = "cold"
    ERASED = "erased"


class ApplicationStatus(StrEnum):
    ACTIVE = "active"
    NEEDS_HR_REVIEW = "needs_hr_review"
    NOT_APPLICATION = "not_application"
    ROLE_UNCLEAR = "role_unclear"
    CLASSIFIED = "classified"
    PARSED = "parsed"
    SCORED = "scored"
    SCREENING_IN_PROGRESS = "screening_in_progress"
    SCREENING_SCORED = "screening_scored"
    SHORTLISTED = "shortlisted"
    SCHEDULED = "scheduled"
    INTERVIEWED = "interviewed"
    REJECTED = "rejected"
    COLD = "cold"
    WITHDRAWN = "withdrawn"


class FitTier(StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class RoleStatus(StrEnum):
    OPEN = "open"
    PAUSED = "paused"
    FILLED = "filled"
    CANCELLED = "cancelled"


class RemotePolicy(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"


# ---------------------------------------------------------------------------
# Parsed resume primitives (Stage 3)
# ---------------------------------------------------------------------------


class Education(BaseModel):
    degree: str
    institution: str
    year: int | None = None


class WorkEntry(BaseModel):
    company: str
    role: str
    duration: str
    highlights: list[str] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    """Projects section — academic, personal, or on-the-job."""

    name: str
    description: str | None = None
    role: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    url: str | None = None
    duration: str | None = None
    highlights: list[str] = Field(default_factory=list)


class AchievementEntry(BaseModel):
    """Awards, hackathons wins, scholarships, recognitions."""

    title: str
    issuer: str | None = None
    year: int | None = None
    description: str | None = None


class PublicationEntry(BaseModel):
    title: str
    venue: str | None = None
    year: int | None = None
    url: str | None = None


class LanguageEntry(BaseModel):
    language: str
    proficiency: str | None = None  # native / fluent / intermediate / basic


class FieldConfidence(BaseModel):
    """Per-field extraction confidence from PARSE_RESUME_V1."""

    name: float = 0.0
    email: float = 0.0
    phone: float = 0.0
    current_ctc_lpa: float = 0.0
    notice_period_days: float = 0.0
    total_years_experience: float = 0.0
    skills: float = 0.0


class CandidateProfile(BaseModel):
    """Structured output of the resume parser. Matches PARSE_RESUME_V1 JSON schema."""

    name: str | None = None
    # Use plain str (not EmailStr). LLM output occasionally includes stray
    # whitespace or malformed strings that EmailStr rejects, which would drop
    # the email from the parse even though a human could read it. The
    # `_clean_email` validator trims + lower-cases; downstream code treats it
    # as best-effort. Mis-formatted emails are still surfaced to HR.
    email: str | None = None
    phone: str | None = None  # E.164 after normalisation

    @field_validator("email", mode="before")
    @classmethod
    def _clean_email(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        cleaned = v.strip().lower()
        if not cleaned or "@" not in cleaned:
            return None
        return cleaned
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    location: str | None = None
    current_role: str | None = None
    current_company: str | None = None
    current_ctc_lpa: float | None = None
    expected_ctc_lpa: float | None = None
    notice_period_days: int | None = None
    total_years_experience: float | None = None
    headline: str | None = None  # short professional tagline or objective
    summary: str | None = None  # longer "About me" / profile paragraph
    education: list[Education] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)  # IDEs, frameworks, platforms beyond core skills
    soft_skills: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)  # industries / verticals worked in
    work_history: list[WorkEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    achievements: list[AchievementEntry] = Field(default_factory=list)
    publications: list[PublicationEntry] = Field(default_factory=list)
    patents: list[str] = Field(default_factory=list)
    open_source: list[str] = Field(default_factory=list)  # repo urls or org/name strings
    certifications: list[str] = Field(default_factory=list)
    languages: list[LanguageEntry] = Field(default_factory=list)
    volunteer_experience: list[str] = Field(default_factory=list)
    extracurriculars: list[str] = Field(default_factory=list)
    availability: str | None = None  # e.g. "immediate joiner", "30-day notice"
    preferred_work_mode: str | None = None  # onsite / hybrid / remote
    willing_to_relocate: bool | None = None
    references_available: bool | None = None
    field_confidence: FieldConfidence = Field(default_factory=FieldConfidence)

    @field_validator("skills", mode="before")
    @classmethod
    def _dedupe_skills(cls, v: Any) -> Any:
        if isinstance(v, list):
            seen: set[str] = set()
            out: list[str] = []
            for s in v:
                key = str(s).strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    out.append(str(s).strip())
            return out
        return v


# ---------------------------------------------------------------------------
# Persisted records (row-shaped Pydantic models)
# ---------------------------------------------------------------------------


class CandidateRecord(BaseModel):
    """Row from `candidates` table."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str | None = None
    phone: str | None = None
    name: str | None = None
    linkedin_url: str | None = None
    status: CandidateStatus = CandidateStatus.INTAKE
    source_channel: SourceChannel
    temporal_workflow_id: str | None = None
    consent_captured_at: datetime | None = None
    consent_text_shown: str | None = None
    created_at: datetime
    updated_at: datetime


class ApplicationRecord(BaseModel):
    """Row from `applications` table."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    candidate_id: UUID
    role_id: UUID | None = None
    status: ApplicationStatus = ApplicationStatus.ACTIVE
    fit_score: int | None = None
    fit_tier: FitTier | None = None
    screening_score: int | None = None
    created_at: datetime
    updated_at: datetime


class ScreeningQuestionSpec(BaseModel):
    """A single question stored inside roles.screening_questions JSONB."""

    id: str
    question: str
    type: str  # "ctc_check" | "notice_period_check" | "must_have_skill" | "scored" | "open_text"
    weight: float = 0.0
    knock_out_value: str | None = None  # e.g. "no" for must-have
    rubric_description: str | None = None
    max_score: int = 10
    options: list[str] | None = None


class InterviewerPanelMember(BaseModel):
    user_id: str
    calendar_email: EmailStr
    role_in_panel: str  # "hiring_manager", "peer", "cross_functional"


class RoleRecord(BaseModel):
    """Row from `roles` table. JSONB columns are typed-modelled here."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    jd_text: str
    screening_questions: list[ScreeningQuestionSpec] = Field(default_factory=list)
    scoring_rubric: dict[str, Any] = Field(default_factory=dict)
    cut_line: int = 60
    interviewer_panel: list[InterviewerPanelMember] = Field(default_factory=list)
    status: RoleStatus = RoleStatus.OPEN
    # Extended attributes (stored in scoring_rubric or separate columns in later migrations)
    ctc_min_lpa: float | None = None
    ctc_max_lpa: float | None = None
    max_notice_days: int | None = None
    location: str | None = None
    remote_policy: RemotePolicy | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Stage 1: Intake payload / result
# ---------------------------------------------------------------------------


class IntakePayload(BaseModel):
    """Normalised form/email/referral input going into the intake activity."""

    source_channel: SourceChannel
    sender_email: EmailStr | None = None
    sender_name: str | None = None
    sender_phone: str | None = None
    subject: str | None = None
    body_text: str | None = None
    attachment_keys: list[str] = Field(default_factory=list)  # pre-uploaded R2 keys
    attachment_filenames: list[str] = Field(default_factory=list)
    referrer_email: EmailStr | None = None
    role_id_hint: UUID | None = None  # from form dropdown
    consent_text_shown: str | None = None
    consent_ip_address: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class IntakeResult(BaseModel):
    candidate_id: UUID
    application_id: UUID
    acknowledgement_sent: bool
    consent_captured: bool
    files_stored: list[str] = Field(default_factory=list)
    was_duplicate: bool = False
