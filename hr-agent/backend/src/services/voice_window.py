"""Quiet-hours clamp for outbound voice calls.

Indian business standard: never dial before 11:00 or after 20:00 IST. The
window is per-tenant configurable via ``settings.voice_call_window_*`` and
optionally skips weekends.

The single public function is :func:`clamp_to_call_window`. It takes a
desired UTC dial time and returns the next moment inside the allowed
window. Idempotent: a time already inside the window is returned
untouched.

Edge cases handled:
  * Naive datetimes are treated as UTC.
  * "Before window today" (e.g. 08:00 IST) -> push forward to today's start.
  * "After window today" (e.g. 22:30 IST) -> push to tomorrow's start.
  * "Past midnight, before window" (e.g. 02:00 IST) -> push to today's start.
  * Weekend skip: lands on Sat/Sun -> push to Monday's start.
  * DST: IST has none, but ``ZoneInfo`` would handle it correctly anyway.

The helper does not enforce a max delay -- that is the caller's job.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from src.config import get_settings


def _push_to_next_start(local: datetime, start_hour: int, skip_weekends: bool) -> datetime:
    """Advance ``local`` to the next ``start_hour:00`` that is not weekend (if asked)."""

    target = local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if target <= local:
        target = target + timedelta(days=1)
    while skip_weekends and target.weekday() >= 5:  # 5=Sat, 6=Sun
        target = target + timedelta(days=1)
    return target


def clamp_to_call_window(
    when_utc: datetime,
    *,
    start_hour: int | None = None,
    end_hour: int | None = None,
    tz_name: str | None = None,
    skip_weekends: bool | None = None,
) -> tuple[datetime, bool]:
    """Return ``(clamped_utc, was_clamped)``.

    ``was_clamped`` is True when the desired time fell outside the window
    and had to be pushed forward.
    """

    settings = get_settings()
    sh = start_hour if start_hour is not None else settings.voice_call_window_start_hour
    eh = end_hour if end_hour is not None else settings.voice_call_window_end_hour
    tz_name = tz_name or settings.voice_call_window_tz
    skip_we = (
        skip_weekends if skip_weekends is not None else settings.voice_call_skip_weekends
    )

    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=UTC)

    # Window disabled (0-24): allow all hours, no clamping needed.
    if sh == 0 and eh >= 24 and not skip_we:
        return when_utc, False

    tz = ZoneInfo(tz_name)
    local = when_utc.astimezone(tz)
    is_weekend = skip_we and local.weekday() >= 5
    in_hours = sh <= local.hour < eh

    if in_hours and not is_weekend:
        return when_utc, False

    # Before window today (and not weekend) -> just push to today's start.
    if not is_weekend and local.hour < sh:
        target = local.replace(hour=sh, minute=0, second=0, microsecond=0)
    else:
        # Past window end OR weekend -> next allowed day's start.
        target = _push_to_next_start(local, sh, skip_we)
    return target.astimezone(UTC), True


def is_in_call_window(when_utc: datetime) -> bool:
    """Convenience predicate for assertions / dashboards."""

    _, clamped = clamp_to_call_window(when_utc)
    return not clamped
