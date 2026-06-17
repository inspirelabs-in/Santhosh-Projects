"""Unified knockout module — single source of truth for disqualification checks.

Replaces duplicated logic in fit_score.py, score_responses.py, and graph.py.
All thresholds come from PolicyRule; callers pass pre-resolved values or
accept defaults.

Knockout types:
- CTC overshoot: expected CTC > role max * multiplier
- Notice period: notice days > role max
- Relocation refusal: won't relocate + on-site role
- Must-have skill: missing required skill (screening-only)
- Location check: location mismatch (screening-only)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KnockoutResult:
    triggered: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def reason_string(self) -> str | None:
        return "; ".join(self.reasons)[:255] or None


def check_ctc_knockout(
    expected_ctc: float | None,
    role_ctc_max: float | None,
    multiplier: float = 1.15,
) -> str | None:
    """Returns knockout reason if CTC exceeds budget, else None."""
    if expected_ctc is None or role_ctc_max is None:
        return None
    if expected_ctc > role_ctc_max * multiplier:
        pct = int((multiplier - 1) * 100)
        return f"expected_ctc_exceeds_budget:{expected_ctc}L>{role_ctc_max}L+{pct}%"
    return None


def check_notice_knockout(
    notice_days: int | None,
    max_notice_days: int | None,
) -> str | None:
    """Returns knockout reason if notice period exceeds max, else None."""
    if notice_days is None or max_notice_days is None:
        return None
    if notice_days > max_notice_days:
        return f"notice_period_exceeds_max:{notice_days}d>{max_notice_days}d"
    return None


def check_relocation_knockout(
    willing_to_relocate: bool | None,
    remote_policy: str | None,
) -> str | None:
    """Returns knockout reason if candidate refuses relocation for on-site role."""
    if willing_to_relocate is False and (remote_policy or "").lower() in {
        "on_site", "onsite", "office",
    }:
        return "candidate_not_willing_to_relocate_onsite_role"
    return None


def check_hard_knockouts(
    *,
    expected_ctc: float | None = None,
    notice_days: int | None = None,
    willing_to_relocate: bool | None = None,
    role_ctc_max: float | None = None,
    role_max_notice_days: int | None = None,
    role_remote_policy: str | None = None,
    ctc_multiplier: float = 1.15,
) -> KnockoutResult:
    """Run all hard knockout checks. Used by fit_score and graph.py."""
    reasons: list[str] = []

    r = check_ctc_knockout(expected_ctc, role_ctc_max, ctc_multiplier)
    if r:
        reasons.append(r)

    r = check_notice_knockout(notice_days, role_max_notice_days)
    if r:
        reasons.append(r)

    r = check_relocation_knockout(willing_to_relocate, role_remote_policy)
    if r:
        reasons.append(r)

    return KnockoutResult(triggered=bool(reasons), reasons=reasons)


def check_screening_knockouts(
    *,
    questions: list,
    responses_by_id: dict,
    profile_expected_ctc: float | None = None,
    profile_notice_days: int | None = None,
    role_ctc_max: float | None = None,
    role_max_notice_days: int | None = None,
    ctc_multiplier: float = 1.15,
) -> tuple[bool, str | None]:
    """Screening-specific knockouts (question-driven). Used by score_responses.

    Returns (triggered, reason) — first match wins.
    """
    from src.models.screening import KnockOutType

    for q in questions:
        if not q.knock_out_value:
            continue

        if q.type == KnockOutType.CTC_CHECK.value:
            expected = profile_expected_ctc
            if expected is None:
                r = responses_by_id.get(q.id)
                try:
                    expected = float(r.answer) if r and r.answer else None
                except (ValueError, AttributeError):
                    expected = None
            reason = check_ctc_knockout(expected, role_ctc_max, ctc_multiplier)
            if reason:
                return True, reason

        elif q.type == KnockOutType.NOTICE_PERIOD_CHECK.value:
            notice = profile_notice_days
            if notice is None:
                r = responses_by_id.get(q.id)
                try:
                    notice = int(r.answer) if r and r.answer else None
                except (ValueError, AttributeError):
                    notice = None
            reason = check_notice_knockout(notice, role_max_notice_days)
            if reason:
                return True, reason

        elif q.type == KnockOutType.MUST_HAVE_SKILL.value:
            r = responses_by_id.get(q.id)
            if r is None or r.answer.strip().lower() in ("no", "false", "0", ""):
                return True, f"missing_must_have:{q.question}"

        elif q.type == KnockOutType.LOCATION_CHECK.value:
            r = responses_by_id.get(q.id)
            if r is None or r.answer.strip().lower() != q.knock_out_value.lower():
                return True, f"location_mismatch:{q.question}"

    return False, None
