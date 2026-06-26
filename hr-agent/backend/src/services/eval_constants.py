"""Canonical evaluation thresholds — single source of truth for all pipeline stages.

Every scoring stage (fit, screening, voice, assignment, interview) uses these
to route a 0-100 score into PASS / NEEDS_REVIEW / FAIL via route_score().
"""

EVAL_PASS_THRESHOLD: int = 70
EVAL_REJECT_BAND: int = 15
