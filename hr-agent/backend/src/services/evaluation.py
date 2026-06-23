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

Threshold + band are global config knobs (``eval_pass_threshold`` /
``eval_reject_band``), tunable without code. Callers may override per-call if a
stage ever needs its own bar.
"""

from __future__ import annotations

from src.models.pipeline import StageVerdict


def route_score(
    score: float | int | None,
    *,
    threshold: int | None = None,
    reject_band: int | None = None,
) -> StageVerdict:
    """Map a 0-100 ``score`` to PASS | NEEDS_REVIEW | FAIL (see module docstring).

    ``threshold`` / ``reject_band`` default to the global config knobs.
    """
    if threshold is None or reject_band is None:
        from src.config import get_settings

        settings = get_settings()
        if threshold is None:
            threshold = settings.eval_pass_threshold
        if reject_band is None:
            reject_band = settings.eval_reject_band

    # No score == we could not judge -> never auto-reject; let a human look.
    if score is None:
        return StageVerdict.NEEDS_REVIEW

    if score >= threshold:
        return StageVerdict.PASS
    if score < threshold - reject_band:
        return StageVerdict.FAIL
    return StageVerdict.NEEDS_REVIEW
