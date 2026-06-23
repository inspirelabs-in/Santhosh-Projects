"""V1 pipeline models: screening generation, evaluation, assignment submission, journey report.

V1 flow: apply -> parse -> generate_screening -> email -> submit ->
evaluate -> (clear_pass: send_assignment | else: needs_hr_review) ->
submit_assignment -> parse_assignment -> journey_report.

These shapes are stored in `applications.screening_questions`,
`applications.screening_evaluation`, `applications.assignment_submission`
(all JSONB). `applications.current_stage` is the state-machine column.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class PipelineStage(StrEnum):
    """Explicit state machine for V1. Stored in applications.current_stage."""

    APPLIED = "applied"
    SCREENING_SENT = "screening_sent"
    SCREENING_SUBMITTED = "screening_submitted"
    SCREENING_EVALUATED = "screening_evaluated"
    NEEDS_HR_REVIEW = "needs_hr_review"
    ASSIGNMENT_SENT = "assignment_sent"
    ASSIGNMENT_SUBMITTED = "assignment_submitted"
    REPORT_READY = "report_ready"
    # --- Agentic V2 stages (voice + assessment + meeting rounds) ---
    VOICE_SCREEN_SCHEDULED = "voice_screen_scheduled"
    VOICE_SCREEN_IN_PROGRESS = "voice_screen_in_progress"
    VOICE_SCREEN_CALLBACK_REQUESTED = "voice_screen_callback_requested"
    VOICE_SCREEN_COMPLETED = "voice_screen_completed"
    VOICE_SCREEN_EVALUATED = "voice_screen_evaluated"
    ASSESSMENT_INVITED = "assessment_invited"
    ASSESSMENT_COMPLETED = "assessment_completed"
    ASSESSMENT_PENDING_REVIEW = "assessment_pending_review"
    ASSESSMENT_EVALUATED = "assessment_evaluated"
    TECHNICAL_MEETING_SCHEDULED = "technical_meeting_scheduled"
    TECHNICAL_MEETING_IN_PROGRESS = "technical_meeting_in_progress"
    TECHNICAL_MEETING_COMPLETED = "technical_meeting_completed"
    TECHNICAL_EVALUATED = "technical_evaluated"
    TECHNICAL_PENDING_APPROVAL = "technical_pending_approval"
    CEO_MEETING_SCHEDULED = "ceo_meeting_scheduled"
    CEO_MEETING_IN_PROGRESS = "ceo_meeting_in_progress"
    CEO_MEETING_COMPLETED = "ceo_meeting_completed"
    CEO_PENDING_APPROVAL = "ceo_pending_approval"
    HR_MEETING_SCHEDULED = "hr_meeting_scheduled"
    HR_MEETING_IN_PROGRESS = "hr_meeting_in_progress"
    HR_MEETING_COMPLETED = "hr_meeting_completed"
    HR_EVALUATED = "hr_evaluated"
    DECISION = "decision"
    OFFER = "offer"
    REJECTED = "rejected"
    HIRED = "hired"


ALLOWED_TRANSITIONS: dict[PipelineStage, set[PipelineStage]] = {
    PipelineStage.APPLIED: {
        PipelineStage.SCREENING_SENT,
        PipelineStage.VOICE_SCREEN_SCHEDULED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.SCREENING_SENT: {PipelineStage.SCREENING_SUBMITTED, PipelineStage.NEEDS_HR_REVIEW, PipelineStage.REJECTED},
    PipelineStage.SCREENING_SUBMITTED: {PipelineStage.SCREENING_EVALUATED, PipelineStage.NEEDS_HR_REVIEW, PipelineStage.REJECTED},
    PipelineStage.SCREENING_EVALUATED: {
        PipelineStage.ASSIGNMENT_SENT,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
        PipelineStage.VOICE_SCREEN_SCHEDULED,
    },
    PipelineStage.NEEDS_HR_REVIEW: {
        PipelineStage.ASSIGNMENT_SENT,
        PipelineStage.REJECTED,
        PipelineStage.SCREENING_SENT,
        PipelineStage.VOICE_SCREEN_SCHEDULED,
    },
    PipelineStage.ASSIGNMENT_SENT: {PipelineStage.ASSIGNMENT_SUBMITTED, PipelineStage.NEEDS_HR_REVIEW, PipelineStage.REJECTED},
    PipelineStage.ASSIGNMENT_SUBMITTED: {PipelineStage.REPORT_READY, PipelineStage.NEEDS_HR_REVIEW, PipelineStage.REJECTED},
    PipelineStage.REPORT_READY: {
        PipelineStage.HIRED,
        PipelineStage.REJECTED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.VOICE_SCREEN_SCHEDULED,
        PipelineStage.TECHNICAL_PENDING_APPROVAL,
    },
    # --- Agentic V2 transitions ---
    PipelineStage.VOICE_SCREEN_SCHEDULED: {
        PipelineStage.VOICE_SCREEN_IN_PROGRESS,
        PipelineStage.VOICE_SCREEN_CALLBACK_REQUESTED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.VOICE_SCREEN_IN_PROGRESS: {
        PipelineStage.VOICE_SCREEN_COMPLETED,
        PipelineStage.VOICE_SCREEN_CALLBACK_REQUESTED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.VOICE_SCREEN_CALLBACK_REQUESTED: {
        PipelineStage.VOICE_SCREEN_SCHEDULED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.VOICE_SCREEN_COMPLETED: {
        PipelineStage.VOICE_SCREEN_EVALUATED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.VOICE_SCREEN_EVALUATED: {
        PipelineStage.ASSESSMENT_INVITED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.ASSESSMENT_INVITED: {
        PipelineStage.ASSESSMENT_COMPLETED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.ASSESSMENT_COMPLETED: {
        PipelineStage.ASSESSMENT_PENDING_REVIEW,
        PipelineStage.ASSESSMENT_EVALUATED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.ASSESSMENT_PENDING_REVIEW: {
        PipelineStage.ASSESSMENT_EVALUATED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.ASSESSMENT_EVALUATED: {
        PipelineStage.TECHNICAL_MEETING_SCHEDULED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.TECHNICAL_MEETING_SCHEDULED: {
        PipelineStage.TECHNICAL_MEETING_IN_PROGRESS,
        PipelineStage.TECHNICAL_MEETING_COMPLETED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.TECHNICAL_MEETING_IN_PROGRESS: {
        PipelineStage.TECHNICAL_MEETING_COMPLETED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.TECHNICAL_MEETING_COMPLETED: {
        PipelineStage.TECHNICAL_EVALUATED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.TECHNICAL_EVALUATED: {
        PipelineStage.TECHNICAL_PENDING_APPROVAL,
        PipelineStage.CEO_MEETING_SCHEDULED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.TECHNICAL_PENDING_APPROVAL: {
        PipelineStage.CEO_MEETING_SCHEDULED,
        PipelineStage.CEO_PENDING_APPROVAL,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.CEO_MEETING_SCHEDULED: {
        PipelineStage.CEO_MEETING_IN_PROGRESS,
        PipelineStage.CEO_MEETING_COMPLETED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.CEO_MEETING_IN_PROGRESS: {
        PipelineStage.CEO_MEETING_COMPLETED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.CEO_MEETING_COMPLETED: {
        PipelineStage.CEO_PENDING_APPROVAL,
        PipelineStage.HR_MEETING_SCHEDULED,
        PipelineStage.HIRED,
        PipelineStage.REJECTED,
        PipelineStage.NEEDS_HR_REVIEW,
    },
    PipelineStage.CEO_PENDING_APPROVAL: {
        PipelineStage.HR_MEETING_SCHEDULED,
        PipelineStage.HIRED,
        PipelineStage.REJECTED,
        PipelineStage.NEEDS_HR_REVIEW,
    },
    PipelineStage.HR_MEETING_SCHEDULED: {
        PipelineStage.HR_MEETING_IN_PROGRESS,
        PipelineStage.HR_MEETING_COMPLETED,
        PipelineStage.HR_EVALUATED,
        PipelineStage.HIRED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.HR_MEETING_IN_PROGRESS: {
        PipelineStage.HR_MEETING_COMPLETED,
        PipelineStage.NEEDS_HR_REVIEW,
        PipelineStage.REJECTED,
    },
    PipelineStage.HR_MEETING_COMPLETED: {
        PipelineStage.HR_EVALUATED,
        PipelineStage.HIRED,
        PipelineStage.REJECTED,
        PipelineStage.NEEDS_HR_REVIEW,
    },
    PipelineStage.HR_EVALUATED: {
        PipelineStage.HIRED,
        PipelineStage.REJECTED,
        PipelineStage.NEEDS_HR_REVIEW,
    },
    PipelineStage.DECISION: {
        PipelineStage.OFFER,
        PipelineStage.REJECTED,
    },
    PipelineStage.OFFER: {
        PipelineStage.HIRED,
        PipelineStage.REJECTED,
    },
    PipelineStage.REJECTED: set(),
    PipelineStage.HIRED: set(),
}


def can_transition(src: PipelineStage, dst: PipelineStage) -> bool:
    return dst in ALLOWED_TRANSITIONS.get(src, set())


class GeneratedQuestion(BaseModel):
    id: str
    question: str
    type: Literal["logistics", "skill_probe", "depth", "open_text", "behavioral", "gap_probe"]
    expected_signal: str
    required: bool = True


class GeneratedScreeningSet(BaseModel):
    """Output of SCREENING_GEN_V1."""

    questions: list[GeneratedQuestion] = Field(default_factory=list)
    generated_at: datetime | None = None
    prompt_version: str | None = None


class ScreeningAnswer(BaseModel):
    question_id: str
    question: str
    answer: str


class ScreeningSubmission(BaseModel):
    """What the candidate submits via /apply/[token]/screening."""

    answers: list[ScreeningAnswer] = Field(default_factory=list)
    submitted_at: datetime | None = None


class PerQuestionScore(BaseModel):
    question_id: str
    score: int
    relevance: Literal["low", "medium", "high"]
    notes: str


class LogisticsCheck(BaseModel):
    ctc_in_range: bool
    notice_acceptable: bool
    location_workable: bool
    rationale: str


class LogisticsValues(BaseModel):
    """Raw values extracted from the candidate's screening answers."""

    current_ctc_lpa: float | None = None
    expected_ctc_lpa: float | None = None
    notice_period_days: int | None = None
    current_location: str | None = None
    willing_to_relocate: bool | None = None


class ScreeningEvaluation(BaseModel):
    """Output of SCREENING_EVAL_V1."""

    overall_score: int
    per_question: list[PerQuestionScore] = Field(default_factory=list)
    logistics_check: LogisticsCheck
    logistics_values: LogisticsValues = Field(default_factory=LogisticsValues)
    red_flags: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    verdict: Literal["clear_pass", "needs_hr_review", "clear_reject"] | None = None
    verdict_rationale: str | None = None
    evaluated_at: datetime | None = None
    prompt_version: str | None = None


class AssignmentFileArtifact(BaseModel):
    r2_key: str
    filename: str
    content_type: str | None = None
    size_bytes: int | None = None
    extracted_text_preview: str | None = None  # first ~2k chars for quick review


class AssignmentSubmission(BaseModel):
    """Stored under applications.assignment_submission JSONB."""

    files: list[AssignmentFileArtifact] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    notes: str | None = None
    submitted_at: datetime | None = None
    project_choice: str | None = None
    deployed_url: str | None = None
    parse_result: dict[str, Any] | None = None  # output of ASSIGNMENT_PARSE_V1


class CompletenessCheck(BaseModel):
    followed_instructions: bool
    covered_requirements: list[str] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)


class QualitySignals(BaseModel):
    depth: Literal["low", "medium", "high"]
    originality: Literal["low", "medium", "high"]
    clarity: Literal["low", "medium", "high"]
    technical_rigor: Literal["low", "medium", "high"]


class AssignmentParseResult(BaseModel):
    """Output of ASSIGNMENT_PARSE_V1."""

    completeness: CompletenessCheck
    quality_signals: QualitySignals
    highlights: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)
    suggested_hr_focus: list[str] = Field(default_factory=list)
    summary: str


class JourneyReport(BaseModel):
    """Markdown report delivered to HR."""

    application_id: UUID
    markdown: str
    generated_at: datetime
    prompt_version: str


# ---------------------------------------------------------------------------
# Agentic V2: Voice screening, Assessment, Meeting analysis shapes
# ---------------------------------------------------------------------------


class VoiceQuestion(BaseModel):
    """Conversational question put to candidate during phone screen."""

    id: str
    question: str
    type: Literal["logistics", "skill_probe", "depth", "behavioral", "open", "background", "work_experience", "company_fit"]
    expected_signal: str
    follow_up_hint: str | None = None


class VoiceAnswer(BaseModel):
    question_id: str
    question: str
    answer_transcript: str
    answer_audio_r2_key: str | None = None
    duration_sec: float | None = None


class EmotionFeatures(BaseModel):
    """[TO BE REMOVED] Paralinguistic features over the full call audio.

    Orphaned: voice scoring moved to Gemini (gemini_audio_eval) which does this
    inline. Only the dead emotion_client stub references this model.
    """

    avg_pitch_hz: float | None = None
    pitch_variance: float | None = None
    speaking_rate_wpm: float | None = None
    pause_ratio: float | None = None
    arousal_score: float | None = None
    valence_score: float | None = None
    dominant_emotion: str | None = None
    confidence_score: float | None = None


class ExtractedCandidateFacts(BaseModel):
    """Structured details extracted from the phone-screen transcript.

    Backfills missing fields on the candidate / application without
    requiring a separate written form. Every field is optional -- the
    evaluator emits only what was clearly stated.
    """

    current_ctc_lpa: float | None = None
    expected_ctc_lpa: float | None = None
    notice_period_days: int | None = None
    current_location: str | None = None
    preferred_location: str | None = None
    willing_to_relocate: bool | None = None
    work_authorization: str | None = None
    total_experience_years: float | None = None
    relevant_experience_years: float | None = None
    current_employer: str | None = None
    current_title: str | None = None
    highest_qualification: str | None = None
    primary_skills: list[str] = Field(default_factory=list)
    languages_spoken: list[str] = Field(default_factory=list)
    notes: str | None = None


class VoiceCallScore(BaseModel):
    """LLM evaluation of the call answers (mirrors ScreeningEvaluation shape)."""

    overall_score: int
    per_question: list[PerQuestionScore] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    verdict: Literal["clear_pass", "needs_hr_review", "clear_reject"] | None = None
    verdict_rationale: str | None = None
    extracted_facts: ExtractedCandidateFacts | None = None
    evaluated_at: datetime | None = None
    prompt_version: str | None = None


class CallKind(StrEnum):
    SCREENING = "screening"
    CONFIRMATION = "confirmation"
    MEETING_SCHEDULE = "meeting_schedule"
    STATUS_UPDATE = "status_update"
    JOINING_DETAILS = "joining_details"
    GENERAL_QUERY = "general_query"


class ProcessingStatus(StrEnum):
    """Tracks OUR ingestion/evaluation of an AI step's RESULT, separate from the
    step's own lifecycle (e.g. VoiceCallStatus). It is the idempotency guard: a
    result is claimed exactly once (pending -> processing), then marked processed
    or failed. Re-delivered webhooks / concurrent recovery paths that find a row
    already ``processing``/``processed`` skip instead of double-running the
    evaluator. Reusable for any AI step (voice, assignment, meeting)."""

    PENDING = "pending"        # result not yet ingested
    PROCESSING = "processing"  # a worker holds the claim (CAS lock)
    PROCESSED = "processed"    # ingested + evaluator enqueued/run
    FAILED = "failed"          # ingestion/eval errored (retryable)


class VoiceCallStatus(StrEnum):
    PENDING = "pending"
    DIALING = "dialing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NO_ANSWER = "no_answer"
    CALLBACK_REQUESTED = "callback_requested"
    DECLINED = "declined"
    VOICEMAIL = "voicemail"


class MeetingRound(StrEnum):
    TECHNICAL = "technical"
    CEO = "ceo"
    HR = "hr"


class EmotionTimelineEntry(BaseModel):
    t_start_sec: float
    t_end_sec: float
    speaker: str
    emotion: str
    confidence: float


class MeetingAnalysis(BaseModel):
    """Output of meeting_analysis activity, stored on meeting_session.report."""

    technical_score: int | None = None
    communication_score: int | None = None
    confidence_score: int | None = None
    overall_score: int
    strengths: list[str] = Field(default_factory=list)

    @field_validator(
        "technical_score",
        "communication_score",
        "confidence_score",
        "overall_score",
        mode="before",
    )
    @classmethod
    def _coerce_score_to_int(cls, v: object) -> object:
        # LLMs often return scores as floats (e.g. 8.5) or numeric strings.
        # Pydantic v2 rejects a fractional float -> int, which previously made
        # the whole meeting analysis fail validation. Round to the nearest int.
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, float):
            return round(v)
        if isinstance(v, str):
            s = v.strip()
            try:
                return round(float(s))
            except ValueError:
                return v
        return v
    red_flags: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    candidate_emotion_timeline: list[EmotionTimelineEntry] = Field(default_factory=list)
    summary: str
    verdict: Literal["clear_pass", "needs_hr_review", "clear_reject"] | None = None
    evaluated_at: datetime | None = None
    prompt_version: str | None = None
