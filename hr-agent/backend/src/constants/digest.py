"""Tunables for the presence-aware "welcome back" recruiter digest.

Single source of truth — imported, never re-declared. These are business
tunables, not infra timers (which live in ``constants/timers.py``); they can be
promoted to runtime ``ConfigSetting``s later if in-product tuning is needed.
"""

from __future__ import annotations

# Minimum time a recruiter must have been away before returning triggers a digest.
AWAY_THRESHOLD_MINUTES = 1

# Suppress a repeat digest within this window (avoids spam on tab-flipping /
# multiple heartbeats close together).
DIGEST_QUIET_MINUTES = 1  # 4 hours

# Cap the lookback window: if away for a week, summarize the last N hours of
# activity rather than the entire absence.
DIGEST_LOOKBACK_MAX_HOURS = 72

# Safety cap on how many events a single digest query pulls.
DIGEST_MAX_EVENTS = 500

# Marks a persisted message as the welcome-back digest so the LLM-history
# builder can fold it into the next user turn instead of emitting it as a
# leading assistant message.
WELCOME_DIGEST_TOOL_MARKER = "__welcome_digest__"
