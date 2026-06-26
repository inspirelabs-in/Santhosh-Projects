"""Shared evaluation routing — the single place that turns a stage's numeric
score into a verdict.

The split, applied identically at every scoring stage (fit, screening, voice,
assignment, technical/ceo/hr interview):

  - The LLM produces a fair 0-100 ``overall_score`` + rationale, with guardrails
    and NO verdict rules and NO threshold in the prompt (so it can't anchor).
  - This function maps that score -> ``StageVerdict`` using a global threshold +
    an ASYMMETRIC review band:

        score >= threshold                    -> PASS
        threshold - reject_band <= score < threshold -> NEEDS_REVIEW (park for HR)
        score < threshold - reject_band       -> FAIL (reject)

The band is asymmetric by design. The owner's stated risk is *losing good
candidates*, not catching bad ones, so only a clearly-below-bar score
auto-rejects; everything borderline goes to a human. A missing/None score never
auto-rejects either -- it routes to NEEDS_REVIEW.

Threshold + band live in ``eval_constants`` (the canonical source) and are also
exposed in the Settings config so operators can tune at deploy time.
Callers may override per-call if a stage ever needs its own bar.
"""

from __future__ import annotations

from src.models.pipeline import StageVerdict
from src.services.eval_constants import EVAL_PASS_THRESHOLD, EVAL_REJECT_BAND


def route_score(
    score: float | int | None,
    *,
    threshold: int | None = None,
    reject_band: int | None = None,
) -> StageVerdict:
    """Map a 0-100 ``score`` to PASS | NEEDS_REVIEW | FAIL (see module docstring).

    ``threshold`` / ``reject_band`` default to ``eval_constants``, with optional
    override from Settings config for deploy-time tuning.
    """
    if threshold is None:
        threshold = EVAL_PASS_THRESHOLD
    if reject_band is None:
        reject_band = EVAL_REJECT_BAND

    # [TO_FIX] SM-8: value-equality ("threshold == EVAL_PASS_THRESHOLD") clobbers a
    # per-stage threshold that a role legitimately set equal to the default constant
    # with the env value. Use a None sentinel before this block to detect "caller did
    # not override". Harmless while env == constant. Left as-is per scope.
    # Allow deploy-time override from env Settings (takes precedence)
    if threshold == EVAL_PASS_THRESHOLD or reject_band == EVAL_REJECT_BAND:
        from src.config import get_settings

        settings = get_settings()
        if threshold == EVAL_PASS_THRESHOLD:
            threshold = settings.eval_pass_threshold
        if reject_band == EVAL_REJECT_BAND:
            reject_band = settings.eval_reject_band

    # No score == we could not judge -> never auto-reject; let a human look.
    if score is None:
        return StageVerdict.NEEDS_REVIEW

    if score >= threshold:
        return StageVerdict.PASS
    if score < threshold - reject_band:
        return StageVerdict.FAIL
    return StageVerdict.NEEDS_REVIEW
