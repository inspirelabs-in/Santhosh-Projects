"""Interview slot, booking, and per-role scheduling configuration."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class InterviewStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    RESCHEDULED = "rescheduled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    CANCELLED = "cancelled"


class TimeSlot(BaseModel):
    """A candidate-facing proposed interview slot. All times timezone-aware (IST by default)."""

    start: datetime
    end: datetime
    interviewer_emails: list[EmailStr] = Field(default_factory=list)
    slot_token: str  # opaque identifier passed back when candidate picks


class InterviewBooking(BaseModel):
    """Row from `interviews` table."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    application_id: UUID
    scheduled_at: datetime | None = None
    calendar_event_id: str | None = None
    meeting_link: str | None = None
    status: InterviewStatus = InterviewStatus.PROPOSED
    transcript_r2_key: str | None = None
    panel_emails: list[EmailStr] = Field(default_factory=list)
    reschedule_count: int = 0
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Per-role scheduling configuration -- persisted under
# ``role.scoring_rubric.scheduling`` so we don't need a new column.
# ---------------------------------------------------------------------------


MeetingRoundLiteral = Literal["technical", "ceo", "hr"]
# Accept any round name for generic scheduling (2 tech rounds, no HR, etc.)
MeetingRoundKey = str


class AvailabilityWindow(BaseModel):
    """One recurring weekly window. ``days`` are 0=Mon ... 6=Sun."""

    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    start_hhmm: str = "14:00"  # 24-hour, panel local time
    end_hhmm: str = "18:00"


class RoundScheduling(BaseModel):
    panel_emails: list[str] = Field(default_factory=list)
    duration_minutes: int = 45
    windows: list[AvailabilityWindow] = Field(default_factory=list)


class RoleScheduling(BaseModel):
    """Read off ``role.scoring_rubric.scheduling``.

    The agent uses these windows + panel emails to propose slots, create the
    Teams meeting, and call the candidate to confirm. When Microsoft Graph
    credentials are present we intersect free/busy with the windows.
    """

    enabled: bool = False
    panel_timezone: str = "Asia/Kolkata"
    candidate_timezone: str | None = None
    rounds: dict[MeetingRoundKey, RoundScheduling] = Field(
        default_factory=lambda: {
            "technical": RoundScheduling(),
            "ceo": RoundScheduling(),
            "hr": RoundScheduling(),
        }
    )
    max_negotiation_attempts: int = 2
    horizon_business_days: int = 10
    min_lead_hours: int = 18

    @classmethod
    def from_role_rubric(cls, rubric: dict | None) -> "RoleScheduling":
        if not rubric:
            return cls()
        sched = rubric.get("scheduling") if isinstance(rubric, dict) else None
        if not sched:
            return cls()
        try:
            return cls.model_validate(sched)
        except Exception:
            return cls()
