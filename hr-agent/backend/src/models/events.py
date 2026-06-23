"""Durable domain-event taxonomy + derived action/notification types.

``domain_events`` is the Postgres source of truth for the work backbone (it
replaces the never-working supervisor engine and the ephemeral Redis-only
events). Redis stays only as the realtime push layer.

The derived inbox reads the actionable subset:
``requires_action = true AND resolved_at IS NULL``.
"""

from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    """Typed events written to ``domain_events.type``."""

    APPLICANT_INTAKE = "applicant_intake"
    RESUME_PARSED = "resume_parsed"
    FIT_SCORED = "fit_scored"
    SCREENING_EVALUATED = "screening_evaluated"
    VOICE_EVALUATED = "voice_evaluated"
    VOICE_CALL_FAILED = "voice_call_failed"
    ASSIGNMENT_SUBMITTED = "assignment_submitted"
    ASSESSMENT_READY = "assessment_ready"  # human gate
    INTERVIEW_SCHEDULED = "interview_scheduled"
    MEETING_ANALYSIS_READY = "meeting_analysis_ready"
    RESCHEDULE_REQUESTED = "reschedule_requested"
    CANDIDATE_EMAIL_REPLY = "candidate_email_reply"
    DECISION_PENDING = "decision_pending"
    STAGE_CHANGED = "stage_changed"
    HIRED = "hired"
    REJECTED = "rejected"


class ActionType(StrEnum):
    """Derived inbox action types (stage-gated work + actionable events)."""

    TRIAGE_NEW_APPLICANT = "triage_new_applicant"
    REVIEW_ASSESSMENT = "review_assessment"
    SCHEDULE_INTERVIEW = "schedule_interview"
    RESCHEDULE_REQUEST = "reschedule_request"
    REVIEW_INTERVIEW_ANALYSIS = "review_interview_analysis"
    NEEDS_REVIEW = "needs_review"  # borderline stage score -> HR confirms pass/reject
    HIRE_OR_REJECT = "hire_or_reject"
    VOICE_CALL_FAILED = "voice_call_failed"
    CANDIDATE_EMAIL_REPLY = "candidate_email_reply"
    JD_INCOMPLETE = "jd_incomplete"


class NotificationType(StrEnum):
    """Informational feed (🔔) — counterpart to action items; read/unread."""

    BOT_JOINED = "bot_joined"
    EMAIL_DELIVERED = "email_delivered"
    STAGE_ADVANCED = "stage_advanced"
    CANDIDATE_VIEWED = "candidate_viewed"
    GENERIC = "generic"
