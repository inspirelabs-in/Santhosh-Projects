"""Interview intelligence: auto feedback requests + cross-reference analysis.

After a meeting is analyzed, automatically sends structured feedback request
emails to the interview panel. When feedback arrives, cross-references
interviewer impressions against pipeline evidence records to detect
discrepancies (e.g., interviewer says "weak communication" but voice screen
scored communication 9/10).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import (
    Application,
    Candidate,
    EvidenceRecord,
    Interview,
    MeetingSession,
    PanelMember,
    Role,
)
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.evidence import record_evidence_batch_verified

logger = logging.getLogger(__name__)

FEEDBACK_CROSS_REF_RULES: list[dict[str, Any]] = [
    {
        "feedback_field": "overall_score",
        "evidence_pattern": "meeting.{round}.overall_score",
        "tolerance": 20,
        "description": "interviewer score vs AI analysis score",
    },
    {
        "feedback_field": "strengths",
        "contra_field": "weaknesses",
        "evidence_pattern": "meeting.{round}.red_flag",
        "check_type": "overlap",
        "description": "interviewer strengths overlap with AI red flags",
    },
    {
        "feedback_field": "weaknesses",
        "contra_field": "strengths",
        "evidence_pattern": "meeting.{round}.strength",
        "check_type": "overlap",
        "description": "interviewer weaknesses overlap with AI strengths",
    },
]


async def send_feedback_requests(
    meeting_session_id: UUID,
    *,
    round_value: str | None = None,
) -> list[str]:
    """Send structured feedback request emails to interview panel after meeting analysis.

    Returns list of emails sent to.
    """
    from src.channels.email import send_email

    sent_to: list[str] = []

    async with session_scope() as session:
        meeting = await session.get(MeetingSession, meeting_session_id)
        if not meeting:
            logger.warning("meeting %s not found", meeting_session_id)
            return sent_to

        round_val = round_value or meeting.round
        app = await session.get(Application, meeting.application_id)
        if not app:
            return sent_to

        candidate = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None

        candidate_name = candidate.name if candidate else "Candidate"
        role_title = role.title if role else "Open Position"

        panel_emails = _extract_panel_emails(meeting, role, round_val)
        if not panel_emails:
            logger.info("no panel emails for meeting %s", meeting_session_id)
            return sent_to

        from src.config import get_settings
        settings = get_settings()
        base_url = settings.app_base_url or "https://app.example.com"
        feedback_url = f"{base_url}/interviews/{meeting.interview_id or meeting.id}/feedback"

        subject = f"Feedback requested: {candidate_name} — {role_title} ({round_val})"
        body = (
            f"Hi,\n\n"
            f"Thank you for interviewing {candidate_name} for the {role_title} position.\n\n"
            f"Please submit your structured feedback using the link below:\n"
            f"{feedback_url}\n\n"
            f"We ask for:\n"
            f"• Your recommendation (hire / no_hire / undecided)\n"
            f"• Key strengths observed\n"
            f"• Areas of concern\n"
            f"• Overall score (0-100)\n"
            f"• Any additional notes\n\n"
            f"Please submit within 24 hours so we can keep the process moving.\n\n"
            f"— HR Agent"
        )

        for email in panel_emails:
            try:
                await send_email(
                    to=email,
                    subject=subject,
                    body=body,
                    idempotency_key=f"feedback_req:{meeting_session_id}:{email}",
                )
                sent_to.append(email)
            except Exception:
                logger.warning("failed to send feedback request to %s", email, exc_info=True)

        if sent_to:
            await log_audit(
                session,
                application_id=meeting.application_id,
                candidate_id=app.candidate_id,
                action="feedback_request_sent",
                actor="agent",
                details={
                    "meeting_session_id": str(meeting_session_id),
                    "round": round_val,
                    "sent_to": sent_to,
                },
            )

    return sent_to


def _extract_panel_emails(
    meeting: MeetingSession,
    role: Role | None,
    round_val: str,
) -> list[str]:
    """Get panel emails from meeting participants or role config."""
    emails: set[str] = set()

    if meeting.participants:
        participants = meeting.participants
        if isinstance(participants, list):
            for p in participants:
                if isinstance(p, dict) and p.get("email"):
                    emails.add(p["email"])
        elif isinstance(participants, dict):
            for v in participants.values():
                if isinstance(v, str) and "@" in v:
                    emails.add(v)

    if not emails and role:
        interview_config = (role.scoring_rubric or {}).get("interview_config", {})
        round_cfg = interview_config.get(round_val, {})
        for e in round_cfg.get("panel_emails", []):
            emails.add(e)

    return list(emails)


async def cross_reference_feedback(
    interview_id: UUID,
    feedback: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare interviewer feedback against evidence records.

    Returns list of discrepancies found.
    """
    discrepancies: list[dict[str, Any]] = []

    async with session_scope() as session:
        interview = await session.get(Interview, interview_id)
        if not interview:
            return discrepancies

        app = await session.get(Application, interview.application_id)
        if not app:
            return discrepancies

        meeting = (
            await session.execute(
                select(MeetingSession)
                .where(MeetingSession.interview_id == interview_id)
                .order_by(MeetingSession.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        round_val = meeting.round if meeting else "technical"

        evidence_rows = (
            await session.execute(
                select(EvidenceRecord)
                .where(EvidenceRecord.application_id == interview.application_id)
                .where(EvidenceRecord.source_stage.like(f"meeting_{round_val}%"))
                .where(EvidenceRecord.superseded_by_id.is_(None))
            )
        ).scalars().all()

        evidence_by_key: dict[str, list[EvidenceRecord]] = {}
        for e in evidence_rows:
            evidence_by_key.setdefault(e.fact_key, []).append(e)

        for rule in FEEDBACK_CROSS_REF_RULES:
            pattern = rule["evidence_pattern"].format(round=round_val)
            check_type = rule.get("check_type", "numeric")

            if check_type == "numeric":
                fb_score = feedback.get(rule["feedback_field"])
                if fb_score is None:
                    continue
                matching = evidence_by_key.get(pattern, [])
                for ev in matching:
                    try:
                        ai_score = float(ev.fact_value) if ev.fact_value is not None else None
                    except (ValueError, TypeError):
                        continue
                    if ai_score is not None:
                        diff = abs(float(fb_score) - ai_score)
                        if diff > rule.get("tolerance", 20):
                            discrepancies.append({
                                "type": "score_mismatch",
                                "description": rule["description"],
                                "interviewer_value": fb_score,
                                "ai_value": ai_score,
                                "difference": diff,
                                "evidence_id": str(ev.id),
                                "round": round_val,
                            })

            elif check_type == "overlap":
                fb_items = feedback.get(rule["feedback_field"], [])
                if not fb_items or not isinstance(fb_items, list):
                    continue
                fb_text = " ".join(str(s).lower() for s in fb_items)
                matching = evidence_by_key.get(pattern, [])
                for ev in matching:
                    ev_text = str(ev.fact_value or "").lower()
                    if not ev_text:
                        continue
                    keywords = [w for w in ev_text.split() if len(w) > 4]
                    overlap_words = [w for w in keywords if w in fb_text]
                    if len(overlap_words) >= 2:
                        discrepancies.append({
                            "type": "contradiction",
                            "description": rule["description"],
                            "interviewer_says": fb_items,
                            "ai_says": ev.fact_value,
                            "overlap_keywords": overlap_words,
                            "evidence_id": str(ev.id),
                            "round": round_val,
                        })

        # Also cross-ref against prior pipeline evidence (voice screen, resume)
        prior_evidence = (
            await session.execute(
                select(EvidenceRecord)
                .where(EvidenceRecord.application_id == interview.application_id)
                .where(EvidenceRecord.source_stage.in_([
                    "voice_screening", "resume_parse", "screening_evaluation",
                ]))
                .where(EvidenceRecord.superseded_by_id.is_(None))
                .where(EvidenceRecord.fact_key.in_([
                    "communication_skills", "technical_depth",
                    "total_experience_years", "primary_skills",
                ]))
            )
        ).scalars().all()

        fb_weaknesses = set(str(w).lower() for w in feedback.get("weaknesses", []))
        fb_strengths = set(str(s).lower() for s in feedback.get("strengths", []))

        for ev in prior_evidence:
            ev_val = str(ev.fact_value or "").lower()
            if ev.fact_key == "communication_skills" and ev_val:
                try:
                    comm_score = float(ev_val)
                    if comm_score >= 7 and any("communic" in w for w in fb_weaknesses):
                        discrepancies.append({
                            "type": "pipeline_contradiction",
                            "description": f"voice screen rated communication {comm_score}/10 but interviewer flagged communication weakness",
                            "source_stage": ev.source_stage,
                            "evidence_id": str(ev.id),
                        })
                    elif comm_score <= 4 and any("communic" in s for s in fb_strengths):
                        discrepancies.append({
                            "type": "pipeline_contradiction",
                            "description": f"voice screen rated communication {comm_score}/10 but interviewer praised communication",
                            "source_stage": ev.source_stage,
                            "evidence_id": str(ev.id),
                        })
                except (ValueError, TypeError):
                    pass

        if discrepancies:
            await log_audit(
                session,
                application_id=interview.application_id,
                candidate_id=app.candidate_id,
                action="feedback_cross_reference_discrepancies",
                actor="agent",
                details={
                    "interview_id": str(interview_id),
                    "discrepancy_count": len(discrepancies),
                    "discrepancies": discrepancies[:10],
                },
            )

            # Record discrepancies as evidence for supervisor awareness
            if len(discrepancies) > 0:
                evidence_batch = []
                for d in discrepancies[:5]:
                    evidence_batch.append({
                        "application_id": interview.application_id,
                        "candidate_id": app.candidate_id,
                        "source_stage": f"feedback_crossref_{round_val}",
                        "source_type": "cross_reference",
                        "extraction_method": "deterministic",
                        "fact_key": f"feedback.discrepancy.{d['type']}",
                        "fact_value": d.get("description", ""),
                        "evidence_text": str(d)[:500],
                    })
                await record_evidence_batch_verified(
                    session,
                    records=evidence_batch,
                    application_id=interview.application_id,
                )

            # Emit supervisor event for discrepancies
            try:
                from src.services.typed_event_bus import EventType, publish_event
                await publish_event(
                    session,
                    event_type=EventType.EVIDENCE_ADDED,
                    application_id=interview.application_id,
                    candidate_id=app.candidate_id,
                    payload={
                        "trigger": "feedback_cross_reference",
                        "interview_id": str(interview_id),
                        "discrepancy_count": len(discrepancies),
                        "round": round_val,
                    },
                )
            except Exception:
                logger.warning("failed to emit cross-ref event", exc_info=True)

    return discrepancies
