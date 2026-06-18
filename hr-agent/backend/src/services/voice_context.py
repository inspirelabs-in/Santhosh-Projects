"""Candidate context assembly for multi-purpose voice calls.

Builds a frozen context snapshot per call so each concurrent call
gets isolated, accurate candidate data with zero cross-contamination.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import (
    Application,
    Candidate,
    CandidateProfileRow,
    Interview,
    MeetingSession,
    Role,
    VoiceCall,
)
from src.models.v1 import VoiceCallStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CandidateCallContext:
    """Immutable snapshot of everything the voice agent needs about a candidate."""

    candidate_name: str
    candidate_phone: str
    role_title: str
    company_name: str
    application_id: UUID
    current_stage: str
    fit_score: int | None = None
    fit_tier: str | None = None
    screening_verdict: str | None = None
    screening_score: int | None = None
    voice_screen_verdict: str | None = None
    voice_screen_score: int | None = None
    meeting_scheduled_at: datetime | None = None
    meeting_link: str | None = None
    meeting_round: str | None = None
    previous_call_summaries: list[str] = field(default_factory=list)
    extracted_facts: dict[str, Any] = field(default_factory=dict)
    role_jd_snippet: str | None = None


async def build_candidate_context(
    session: AsyncSession,
    application_id: UUID,
    company_name: str = "GrabOn",
) -> CandidateCallContext:
    """Assemble full candidate context from DB for voice call injection."""

    app = await session.get(Application, application_id)
    if app is None:
        raise ValueError(f"application {application_id} not found")

    candidate = await session.get(Candidate, app.candidate_id)
    if candidate is None:
        raise ValueError(f"candidate {app.candidate_id} not found")

    role = await session.get(Role, app.role_id) if app.role_id else None

    # Screening evaluation
    screening_eval = app.screening_evaluation or {}
    screening_verdict = screening_eval.get("verdict")
    screening_score = app.screening_score

    # Latest voice screen result
    voice_row = (
        await session.execute(
            select(VoiceCall)
            .where(
                VoiceCall.application_id == application_id,
                VoiceCall.call_kind == "screening",
                VoiceCall.status == VoiceCallStatus.COMPLETED.value,
            )
            .order_by(VoiceCall.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    voice_verdict = voice_row.verdict if voice_row else None
    voice_score = voice_row.overall_score if voice_row else None

    # Previous call summaries (last 3 completed calls of any kind)
    prior_calls = (
        await session.execute(
            select(VoiceCall)
            .where(
                VoiceCall.application_id == application_id,
                VoiceCall.status == VoiceCallStatus.COMPLETED.value,
            )
            .order_by(VoiceCall.created_at.desc())
            .limit(3)
        )
    ).scalars().all()

    summaries = []
    for call in prior_calls:
        kind = getattr(call, "call_kind", "screening")
        duration = f"{call.duration_sec:.0f}s" if call.duration_sec else "unknown"
        summary = f"[{kind}] duration={duration}"
        if call.verdict:
            summary += f" verdict={call.verdict}"
        if call.overall_score is not None:
            summary += f" score={call.overall_score}"
        summaries.append(summary)

    # Upcoming meeting
    interview = (
        await session.execute(
            select(Interview)
            .where(
                Interview.application_id == application_id,
                Interview.status.in_(["proposed", "confirmed", "scheduled"]),
            )
            .order_by(Interview.scheduled_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()

    meeting_session = None
    if interview:
        meeting_session = (
            await session.execute(
                select(MeetingSession)
                .where(MeetingSession.interview_id == interview.id)
                .limit(1)
            )
        ).scalar_one_or_none()

    # Candidate profile facts
    profile_row = (
        await session.execute(
            select(CandidateProfileRow)
            .where(CandidateProfileRow.candidate_id == candidate.id)
            .order_by(CandidateProfileRow.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    extracted_facts = {}
    if profile_row and isinstance(profile_row.parsed_data, dict):
        extracted_facts = profile_row.parsed_data

    return CandidateCallContext(
        candidate_name=candidate.name or "Candidate",
        candidate_phone=candidate.phone or "",
        role_title=role.title if role else "Unknown Role",
        company_name=company_name,
        application_id=application_id,
        current_stage=app.current_stage,
        fit_score=app.fit_score,
        fit_tier=app.fit_tier,
        screening_verdict=screening_verdict,
        screening_score=screening_score,
        voice_screen_verdict=voice_verdict,
        voice_screen_score=voice_score,
        meeting_scheduled_at=interview.scheduled_at if interview else None,
        meeting_link=interview.meeting_link if interview else None,
        meeting_round=meeting_session.round if meeting_session else None,
        previous_call_summaries=summaries,
        extracted_facts=extracted_facts,
        role_jd_snippet=(role.jd_text[:500] if role and role.jd_text else None),
    )


def format_context_for_prompt(ctx: CandidateCallContext) -> str:
    """Format candidate context as a text block for system prompt injection."""

    lines = [
        "=== CANDIDATE CONTEXT (confidential, do not read verbatim) ===",
        f"Name: {ctx.candidate_name}",
        f"Role: {ctx.role_title} at {ctx.company_name}",
        f"Current stage: {ctx.current_stage}",
    ]

    if ctx.fit_score is not None:
        lines.append(f"Fit score: {ctx.fit_score}/100 (tier: {ctx.fit_tier or 'n/a'})")

    if ctx.screening_score is not None:
        lines.append(f"Screening score: {ctx.screening_score}/100 (verdict: {ctx.screening_verdict or 'n/a'})")

    if ctx.voice_screen_score is not None:
        lines.append(f"Voice screen score: {ctx.voice_screen_score}/100 (verdict: {ctx.voice_screen_verdict or 'n/a'})")

    if ctx.meeting_scheduled_at:
        lines.append(f"Next meeting: {ctx.meeting_scheduled_at.strftime('%Y-%m-%d %H:%M')} ({ctx.meeting_round or 'unknown'} round)")
        if ctx.meeting_link:
            lines.append(f"Meeting link: {ctx.meeting_link}")

    if ctx.previous_call_summaries:
        lines.append(f"Previous calls: {'; '.join(ctx.previous_call_summaries)}")

    facts = ctx.extracted_facts
    if facts:
        key_display = {
            "total_experience_years": "Experience (yrs)",
            "current_ctc_lpa": "Current CTC (LPA)",
            "expected_ctc_lpa": "Expected CTC (LPA)",
            "notice_period_days": "Notice period (days)",
            "current_location": "Location",
            "willing_to_relocate": "Willing to relocate",
        }
        for key, label in key_display.items():
            val = facts.get(key)
            if val is not None:
                lines.append(f"{label}: {val}")

    lines.append("=== END CONTEXT ===")
    return "\n".join(lines)
