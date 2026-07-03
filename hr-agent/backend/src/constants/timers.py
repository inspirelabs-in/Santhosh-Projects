"""Every polling / interval / timeout timer used by the arq worker and the
background-loop services (auto-nudge, stall detector, webhook watchdog, mail
ingest, config store, recruiter nudge worker).

This module holds ONLY timing values -- magic numbers that control cadence,
timeouts, and lookback/lookahead windows. It intentionally does NOT include
business-rule tables such as ``_STAGE_SLA_HOURS`` in ``workers/jobs.py``,
which configure per-stage SLA policy rather than a loop's own timing.

Extracted as part of a behavior-preserving refactor: every value below is
IDENTICAL to the literal it replaced, with the single deliberate exception of
``ARQ_POLL_DELAY_SECONDS`` (see comment on that constant).

The inline comment beside each constant is the value in human terms; the
docstring below it says what the timer controls.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# arq WorkerSettings (backend/src/workers/main.py)
# ---------------------------------------------------------------------------

ARQ_JOB_TIMEOUT_SECONDS = 600  # 10 minutes
"""Default max runtime for a single arq job."""

ARQ_KEEP_RESULT_SECONDS = 60 * 60  # 1 hour
"""How long arq retains a job's result in Redis after completion."""

ARQ_POLL_DELAY_SECONDS = 15  # 15 seconds (raised from arq's 0.5s default)
"""Interval between arq's Redis polls for new jobs.

Deliberate change (not behavior-preserving): arq's built-in default is 0.5s,
which polls Redis ~2x/sec, 24/7. That constant polling is what drained the
Upstash free-tier command quota. Raising it to 15s cuts Redis command volume
by ~30x at the cost of job pickup latency rising from ~0.5s to ~15s, which is
an acceptable tradeoff for this workload.
"""

# ---------------------------------------------------------------------------
# arq cron schedules (backend/src/workers/main.py) -- second/minute/hour sets.
# Values are unchanged from the inline literals; only given names here.
# ---------------------------------------------------------------------------

POLL_DUE_CALLBACKS_CRON_SECONDS = {0, 15, 30, 45}  # every 15 seconds
"""poll_due_callbacks cron: every 15 seconds."""

CAMPAIGN_DISPATCH_TICK_CRON_SECONDS = {7, 22, 37, 52}  # every 15 seconds, offset
"""campaign_dispatch_tick cron: every 15 seconds, offset from callbacks poll."""

RECONCILE_STUCK_VOICE_CALLS_CRON_MINUTES = {3, 18, 33, 48}  # every 15 minutes
"""reconcile_stuck_voice_calls cron: every 15 minutes."""

PIPELINE_SLA_MONITOR_CRON_MINUTES = {7}  # hourly, at HH:07
"""pipeline_sla_monitor cron: once per hour at HH:07."""

PRUNE_OLD_ARTIFACTS_CRON_HOURS = {3}  # daily, 03:xx UTC (hour part)
"""prune_old_artifacts cron: daily, hour component (03:00 UTC)."""

PRUNE_OLD_ARTIFACTS_CRON_MINUTES = {11}  # ...:11 -> daily at 03:11 UTC
"""prune_old_artifacts cron: daily, minute component (03:11 UTC)."""

ASSIGNMENT_DEADLINE_REMINDERS_CRON_MINUTES = {0, 30}  # every 30 minutes
"""assignment_deadline_reminders cron: every 30 minutes."""

RECONCILE_STUCK_MEETINGS_CRON_MINUTES = {10, 25, 40, 55}  # every 15 minutes, offset
"""reconcile_stuck_meetings cron: every 15 minutes, offset from voice reconciler."""

# ---------------------------------------------------------------------------
# workers/jobs.py -- polling / reconcile windows
# ---------------------------------------------------------------------------

POLL_DUE_CALLBACKS_HORIZON_SECONDS = 30  # look 30 seconds ahead
"""poll_due_callbacks: look this far ahead of "now" for due callback_at rows."""

VOICE_CALL_NO_PICKUP_CUTOFF_MINUTES = 5  # 5 minutes
"""reconcile_stuck_voice_calls: a dialing call with no started_at after this
many minutes is considered stuck (never rang / connected)."""

VOICE_CALL_OVER_RUNTIME_GRACE_SECONDS = 30  # 30 seconds
"""reconcile_stuck_voice_calls: grace period added on top of
``voice_agent_max_call_seconds`` before an in-progress call is considered
stuck (over-runtime)."""

VOICE_CALL_EVAL_RETRY_CUTOFF_MINUTES = 5  # 5 minutes
"""reconcile_stuck_voice_calls: a completed call with no evaluation verdict
after this many minutes is retried."""

MEETING_ANALYSIS_STUCK_CUTOFF_MINUTES = 10  # 10 minutes
"""reconcile_stuck_meetings: a meeting whose bot finished but whose analysis
never ran is retried after this many minutes."""

MEETING_IN_CALL_STUCK_CUTOFF_HOURS = 3  # 3 hours
"""reconcile_stuck_meetings: a meeting stuck in bot_status=in_call for this
long (bot never reported completion) is marked failed."""

ASSIGNMENT_DEADLINE_REMINDER_WINDOW_HOURS = 24  # 24 hours
"""assignment_deadline_reminders: send a reminder when the deadline is within
this many hours."""

PRUNE_OLD_ARTIFACTS_MIN_RETENTION_DAYS = 1  # 1 day (floor)
"""prune_old_artifacts: floor applied to the configurable
``data_retention_days_default`` setting."""

# ---------------------------------------------------------------------------
# services/auto_nudge.py
# ---------------------------------------------------------------------------

AUTO_NUDGE_INITIAL_DELAY_SECONDS = 300  # 5 minutes
"""auto_nudge worker: initial delay before the first pass, to let the app boot."""

AUTO_NUDGE_LOOP_INTERVAL_SECONDS = 21600  # 6 hours
"""auto_nudge worker: steady-state loop interval."""

# ---------------------------------------------------------------------------
# services/stall_detector.py
# ---------------------------------------------------------------------------

STALL_DETECTOR_LOOP_INTERVAL_SECONDS = 7200  # 2 hours
"""stall detector: steady-state loop interval."""

# ---------------------------------------------------------------------------
# services/webhook_watchdog.py
# ---------------------------------------------------------------------------

WEBHOOK_WATCHDOG_INITIAL_DELAY_SECONDS = 600  # 10 minutes
"""webhook watchdog: initial delay before the first pass."""

WEBHOOK_WATCHDOG_LOOP_INTERVAL_SECONDS = 1800  # 30 minutes
"""webhook watchdog: steady-state loop interval."""

# ---------------------------------------------------------------------------
# services/mail_ingest.py
# ---------------------------------------------------------------------------

MAIL_POLLER_IDLE_LOOP_SECONDS = 3600  # 1 hour (idle-only fallback)
"""mail poller: sleep interval used only when no inboxes are configured
(idle branch). The real poll interval is configurable via
``settings.mail_poll_interval_seconds`` and is intentionally left as-is."""

# ---------------------------------------------------------------------------
# Crash-retry backoffs (services/config_store.py, services/recruiter_nudge_worker.py)
# ---------------------------------------------------------------------------

CRASH_RETRY_BACKOFF_SECONDS = 5  # 5 seconds
"""Generic backoff before retrying a background loop after an unhandled
crash (config invalidation listener, recruiter nudge worker)."""
