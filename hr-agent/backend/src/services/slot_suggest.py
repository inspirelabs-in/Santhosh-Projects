"""Suggest interview slots across upcoming business days (excludes Sat/Sun).

Used by the chat-driven scheduling flow: when a candidate requests a
reschedule, Pulse offers the recruiter a short list of sensible slots in the
next few business days so they can pick one in chat.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Local hours of day we offer each business day.
_DEFAULT_HOURS: tuple[int, ...] = (11, 15)


def suggest_slots(
    *,
    now: datetime | None = None,
    business_days: int = 5,
    hours: tuple[int, ...] = _DEFAULT_HOURS,
    tz: str = "Asia/Kolkata",
    min_lead_hours: int = 12,
    limit: int = 6,
) -> list[datetime]:
    """Return upcoming slot start times as tz-aware UTC datetimes.

    Walks the next ``business_days`` business days (Mon–Fri only), proposing
    each hour in ``hours`` (local time ``tz``). Skips any slot sooner than
    ``min_lead_hours`` from ``now``. Caps the result at ``limit`` slots.
    """
    zone = ZoneInfo(tz)
    now = now or datetime.now(tz=UTC)
    local_now = now.astimezone(zone)
    earliest = now + timedelta(hours=min_lead_hours)

    out: list[datetime] = []
    day_offset = 0
    seen_business_days = 0
    # Guard against pathological configs (e.g. all hours filtered) — cap the walk.
    while seen_business_days < business_days and day_offset <= business_days + 7:
        d = (local_now + timedelta(days=day_offset)).date()
        day_offset += 1
        if d.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
            continue
        seen_business_days += 1
        for h in hours:
            slot_local = datetime.combine(d, time(hour=h), tzinfo=zone)
            slot_utc = slot_local.astimezone(UTC)
            if slot_utc <= earliest:
                continue
            out.append(slot_utc)
            if len(out) >= limit:
                return out
    return out


def format_slot(dt: datetime, *, tz: str = "Asia/Kolkata") -> str:
    """Human-readable slot label in the given timezone, e.g.
    'Mon 23 Jun, 3:00 PM IST'.
    """
    zone = ZoneInfo(tz)
    local = dt.astimezone(zone)
    tz_abbr = local.tzname() or tz
    # %-I is not portable on Windows; strip leading zero manually.
    hour12 = local.strftime("%I").lstrip("0") or "12"
    return f"{local.strftime('%a %d %b')}, {hour12}:{local.strftime('%M %p')} {tz_abbr}"
