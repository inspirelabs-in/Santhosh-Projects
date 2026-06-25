"""Pydantic schemas — the source of truth for every layer of the hiring agent."""

from src.models.audit import AuditEntry, ConsentArtifact
from src.models.candidate import (
    ApplicationRecord,
    CandidateProfile,
    CandidateRecord,
    Education,
    FieldConfidence,
    IntakePayload,
    IntakeResult,
    RoleRecord,
    WorkEntry,
)
from src.models.llm_outputs import (
    ClassificationResult,
    FitAssessment,
    InterviewReport,
    OpenTextScore,
    RejectionDraft,
)
from src.models.scheduling import InterviewBooking, InterviewStatus, TimeSlot
from src.models.screening import (
    KnockOutType,
    ScoreResult,
    ScreeningQuestion,
    ScreeningResponse,
    ScreeningResponseItem,
)

__all__ = [
    "ApplicationRecord",
    "AuditEntry",
    "CandidateProfile",
    "CandidateRecord",
    "ClassificationResult",
    "ConsentArtifact",
    "Education",
    "FieldConfidence",
    "FitAssessment",
    "IntakePayload",
    "IntakeResult",
    "InterviewBooking",
    "InterviewReport",
    "InterviewStatus",
    "KnockOutType",
    "OpenTextScore",
    "RejectionDraft",
    "RoleRecord",
    "ScoreResult",
    "ScreeningQuestion",
    "ScreeningResponse",
    "ScreeningResponseItem",
    "TimeSlot",
    "WorkEntry",
]
