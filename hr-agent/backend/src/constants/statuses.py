"""Cross-cutting `Application.status` / pipeline-stage value groupings shared
by the API routers and background services.

Extracted as part of a behavior-preserving refactor: every value below is
IDENTICAL to the literal(s) it replaced.
"""

from __future__ import annotations

TERMINAL_APPLICATION_STATUSES = frozenset({"rejected", "hired", "withdrawn"})
"""`Application.status` values that mark an application as no longer active.

Used with `Application.status.in_(...)` / `.notin_(...)` (SQLAlchemy accepts
any iterable collection, including a frozenset) across analytics, webhook
inbound-call/SMS/WhatsApp handlers, mail ingest, stall detector, and webhook
watchdog. NOTE: this is deliberately a 3-member set; it is NOT the same as
`REJECTED_OR_WITHDRAWN_STATUSES` below (which intentionally still counts
"hired" candidates as active/ranked) or `auto_nudge.py`'s inline 4-member
filter (which additionally excludes "cold" leads) -- those are distinct
concepts and are left as their own literals.
"""

REJECTED_OR_WITHDRAWN_STATUSES = frozenset({"rejected", "withdrawn"})
"""`Application.status` values meaning the application was actively closed
out without a hire. Deliberately EXCLUDES "hired" -- callers use this to
filter out dead applications while still treating hired candidates as
active/rankable. Used by `candidate_ranking.py` (exclude from stack-rank
unless `include_rejected`) and `auto_progress.py` (skip auto-progression for
inactive applications). NOT the same as `TERMINAL_APPLICATION_STATUSES`
above.
"""

ROLE_STATUSES_AUTO_ACTIVATED_BY_ASSIGNMENT_DOC = ("draft", "paused")
"""`Role.status` values that get auto-flipped to `"open"` once an assignment
problem doc/PDF is attached (upload, publish, or agent-generated). Identical
literal + identical logic in `api/roles.py` (upload + publish endpoints) and
`recruiter_agent/tools.py` (Pulse assignment-gen tool). NOTE: NOT the same
concept as `api/v1_campaigns.py`'s `("draft", "paused")` gate on
`Campaign.status` -- same value, different entity/purpose -- left separate.
"""

ACTIVE_MEETING_SESSION_BOT_STATUSES = ("pending", "scheduled", "in_call")
"""`MeetingSession.bot_status` values meaning a meeting bot dispatch is still
in flight (not yet completed/failed/cancelled). Used with
`MeetingSession.bot_status.in_(...)` for idempotency checks before scheduling
a new meeting (`activities/v1_schedule_meeting.py`, `services/chat_meeting.py`)
and for the "in flight" agent-status snapshot (`api/agent_status.py`). NOTE:
this is NOT the same as `services/scheduling.py`'s 4-member
`("pending", "scheduled", "in_call", "done")` (which intentionally also
blocks out *completed* meetings when checking panel free/busy) or
`services/webhook_watchdog.py`'s narrower `("pending", "joining")` (used for
a different staleness check) -- those are distinct concepts and are left as
their own literals.
"""
