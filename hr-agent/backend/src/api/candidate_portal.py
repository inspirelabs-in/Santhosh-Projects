"""Candidate-facing transparency portal.

Lets candidates check their application status, see what stage they're in,
timeline of events, and expected next steps. Reduces inbound "where am I?"
queries and builds trust in the process.

Auth: HMAC-signed token containing application_id, so candidates can only
see their own application. Token is sent in initial chat invite / email.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.config import get_settings
from src.db.base import Application, AuditLog, Candidate, MeetingSession, Role
from src.db.connection import session_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portal", tags=["candidate-portal"])

# Stage display names and descriptions (candidate-friendly)
STAGE_DISPLAY: dict[str, dict[str, str]] = {
    "applied": {
        "label": "Application Received",
        "description": "We've received your application and are reviewing it.",
        "next": "Our team will review your profile and get back to you shortly.",
    },
    "screening_sent": {
        "label": "Screening Questions Sent",
        "description": "We've sent you some screening questions to learn more about you.",
        "next": "Please complete the screening questions at your earliest convenience.",
    },
    "screening_submitted": {
        "label": "Screening Completed",
        "description": "Thank you for completing the screening questions!",
        "next": "Our team is reviewing your responses.",
    },
    "screening_evaluated": {
        "label": "Screening Reviewed",
        "description": "We've reviewed your screening responses.",
        "next": "You'll hear from us about next steps soon.",
    },
    "voice_screen_scheduled": {
        "label": "Voice Screen Scheduled",
        "description": "A voice screening call has been scheduled.",
        "next": "Please be available for the scheduled call.",
    },
    "voice_screen_completed": {
        "label": "Voice Screen Completed",
        "description": "Thank you for completing the voice screening!",
        "next": "Our team is evaluating the conversation.",
    },
    "voice_screen_evaluated": {
        "label": "Voice Screen Reviewed",
        "description": "Your voice screening has been reviewed.",
        "next": "Next steps will be shared shortly.",
    },
    "assessment_invited": {
        "label": "Assessment Invited",
        "description": "We've invited you to complete a technical assessment.",
        "next": "Please complete the assessment by the deadline provided.",
    },
    "assessment_completed": {
        "label": "Assessment Submitted",
        "description": "Thank you for submitting your assessment!",
        "next": "Our technical team is reviewing your submission.",
    },
    "assessment_evaluated": {
        "label": "Assessment Reviewed",
        "description": "Your assessment has been reviewed.",
        "next": "You'll hear about next steps soon.",
    },
    "technical_meeting_scheduled": {
        "label": "Technical Interview Scheduled",
        "description": "Your technical interview has been scheduled.",
        "next": "Please join the meeting at the scheduled time.",
    },
    "technical_meeting_completed": {
        "label": "Technical Interview Completed",
        "description": "Thank you for completing the technical interview!",
        "next": "The interview panel is reviewing their notes.",
    },
    "ceo_meeting_scheduled": {
        "label": "Leadership Interview Scheduled",
        "description": "A leadership interview has been scheduled.",
        "next": "Please join the meeting at the scheduled time.",
    },
    "ceo_meeting_completed": {
        "label": "Leadership Interview Completed",
        "description": "Thank you for completing the leadership interview!",
        "next": "We're finalizing the evaluation.",
    },
    "hr_meeting_scheduled": {
        "label": "HR Discussion Scheduled",
        "description": "An HR discussion has been scheduled.",
        "next": "Please join the meeting at the scheduled time.",
    },
    "hr_meeting_completed": {
        "label": "HR Discussion Completed",
        "description": "Thank you for the HR discussion!",
        "next": "We're working on the final decision.",
    },
    "offer_extended": {
        "label": "Offer Extended",
        "description": "Congratulations! We've extended an offer to you.",
        "next": "Please review the offer details and let us know your decision.",
    },
    "hired": {
        "label": "Welcome Aboard!",
        "description": "You've accepted the offer. Welcome to the team!",
        "next": "Our HR team will reach out with onboarding details.",
    },
    "rejected": {
        "label": "Application Closed",
        "description": "Thank you for your interest. Unfortunately, we've decided to move forward with other candidates.",
        "next": "We appreciate your time and wish you the best in your job search.",
    },
}

# Stages that need HR review — show generic "under review"
HIDDEN_STAGES = {
    "needs_hr_review", "technical_pending_approval",
    "ceo_pending_approval", "hr_evaluated",
}


def generate_portal_token(application_id: UUID) -> str:
    """Generate HMAC token for candidate portal access."""
    settings = get_settings()
    secret = (settings.secret_key or "default-secret").encode()
    msg = str(application_id).encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()[:32]
    return f"{application_id}:{sig}"


def verify_portal_token(token: str) -> UUID | None:
    """Verify portal token and return application_id if valid."""
    settings = get_settings()
    secret = (settings.secret_key or "default-secret").encode()

    parts = token.split(":", 1)
    if len(parts) != 2:
        return None

    app_id_str, provided_sig = parts
    try:
        app_id = UUID(app_id_str)
    except ValueError:
        return None

    expected_sig = hmac.new(secret, str(app_id).encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(provided_sig, expected_sig):
        return None

    return app_id


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TimelineEvent(BaseModel):
    date: str
    label: str
    description: str | None = None


class PortalStatusResponse(BaseModel):
    candidate_name: str
    role_title: str
    current_stage_label: str
    current_stage_description: str
    next_steps: str
    applied_at: str
    last_updated: str
    timeline: list[TimelineEvent]
    progress_percent: int = Field(ge=0, le=100)


# Pipeline order for progress calculation
STAGE_ORDER = [
    "applied", "screening_sent", "screening_submitted", "screening_evaluated",
    "voice_screen_scheduled", "voice_screen_completed", "voice_screen_evaluated",
    "assessment_invited", "assessment_completed", "assessment_evaluated",
    "technical_meeting_scheduled", "technical_meeting_completed",
    "ceo_meeting_scheduled", "ceo_meeting_completed",
    "hr_meeting_scheduled", "hr_meeting_completed",
    "offer_extended", "hired",
]


@router.get("/status", response_model=PortalStatusResponse)
async def portal_status(
    token: str = Query(..., description="Portal access token"),
) -> PortalStatusResponse:
    """Get candidate-facing application status."""
    app_id = verify_portal_token(token)
    if not app_id:
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    async with session_scope() as session:
        app = await session.get(Application, app_id)
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")

        candidate = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None

        # Build timeline from audit log (candidate-safe events only)
        audit_rows = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.application_id == app_id)
                .where(AuditLog.action.in_([
                    "stage_set", "screening_sent", "assignment_invited",
                    "meeting_scheduled", "offer_extended",
                    "feedback_request_sent",
                ]))
                .order_by(AuditLog.created_at.asc())
                .limit(30)
            )
        ).scalars().all()

    candidate_name = candidate.name if candidate else "Candidate"
    role_title = role.title if role else "Open Position"

    raw_stage = app.current_stage or "applied"
    if raw_stage in HIDDEN_STAGES:
        display_stage = "screening_evaluated"
        stage_info = {
            "label": "Under Review",
            "description": "Your application is currently being reviewed by our team.",
            "next": "We'll update you as soon as we have more information.",
        }
    else:
        display_stage = raw_stage
        stage_info = STAGE_DISPLAY.get(raw_stage, {
            "label": raw_stage.replace("_", " ").title(),
            "description": "Your application is being processed.",
            "next": "We'll keep you updated on next steps.",
        })

    # Calculate progress
    if raw_stage == "rejected":
        progress = 100
    elif raw_stage == "hired":
        progress = 100
    elif display_stage in STAGE_ORDER:
        idx = STAGE_ORDER.index(display_stage)
        progress = int((idx / (len(STAGE_ORDER) - 1)) * 100)
    else:
        progress = 10

    # Build timeline
    timeline: list[TimelineEvent] = []
    seen_stages: set[str] = set()
    for audit in audit_rows:
        details = audit.details or {}
        new_stage = details.get("new_stage", "")
        if new_stage and new_stage in seen_stages:
            continue
        if new_stage:
            seen_stages.add(new_stage)

        label = _audit_to_label(audit.action, details)
        if label:
            timeline.append(TimelineEvent(
                date=audit.created_at.strftime("%b %d, %Y"),
                label=label,
            ))

    if not timeline:
        timeline.append(TimelineEvent(
            date=app.created_at.strftime("%b %d, %Y"),
            label="Application Received",
        ))

    return PortalStatusResponse(
        candidate_name=candidate_name,
        role_title=role_title,
        current_stage_label=stage_info["label"],
        current_stage_description=stage_info["description"],
        next_steps=stage_info["next"],
        applied_at=app.created_at.strftime("%b %d, %Y"),
        last_updated=app.updated_at.strftime("%b %d, %Y") if app.updated_at else app.created_at.strftime("%b %d, %Y"),
        timeline=timeline,
        progress_percent=progress,
    )


def _audit_to_label(action: str, details: dict) -> str | None:
    """Convert audit action to candidate-friendly timeline label."""
    new_stage = details.get("new_stage", "")

    if action == "stage_set" and new_stage:
        info = STAGE_DISPLAY.get(new_stage)
        if info:
            return info["label"]
        if new_stage in HIDDEN_STAGES:
            return "Under Review"
        return new_stage.replace("_", " ").title()

    if action == "screening_sent":
        return "Screening Questions Sent"
    if action == "assignment_invited":
        return "Assessment Invitation Sent"
    if action == "meeting_scheduled":
        round_val = details.get("round", "")
        if round_val == "technical":
            return "Technical Interview Scheduled"
        if round_val == "ceo":
            return "Leadership Interview Scheduled"
        return "Interview Scheduled"
    if action == "offer_extended":
        return "Offer Extended"

    return None


@router.get("/generate-token")
async def generate_token(
    application_id: UUID = Query(...),
) -> dict[str, str]:
    """Generate portal token for an application. Internal use — called when
    sending chat invites or emails to candidates."""
    token = generate_portal_token(application_id)
    settings = get_settings()
    base_url = settings.app_base_url or "https://app.example.com"
    return {
        "token": token,
        "url": f"{base_url}/portal?token={token}",
    }
