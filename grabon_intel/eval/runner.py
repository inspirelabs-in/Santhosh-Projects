"""Eval runner — runs the LangGraph supervisor on a list of GoldenCases,
checks each expectation, computes pass-rate + per-case detail.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from ..graph import run_research
from ..logging import get_logger
from .golden import GoldenCase

log = get_logger(__name__)


@dataclasses.dataclass(slots=True)
class EvalRecord:
    name: str
    passed: bool
    failures: list[str]
    score_total: int | None
    tier: str | None
    cost_cents: int


@dataclasses.dataclass(slots=True)
class EvalReport:
    started_at: str
    finished_at: str
    n_cases: int
    n_passed: int
    pass_rate: float
    total_cost_cents: int
    records: list[EvalRecord]


def _check(expected: dict[str, Any], out) -> list[str]:
    failures: list[str] = []
    research = out.research or {}
    opp = out.opportunity or {}
    score = out.score or {}

    cat = (research.get("positioning") or {}).get("category", "")
    cc = expected.get("category_contains")
    if cc:
        cat_l = cat.lower() if isinstance(cat, str) else ""
        if not any(c.lower() in cat_l for c in cc):
            failures.append(f"category_contains: got '{cat}', expected substr of {cc}")

    services = [
        (s.get("service") if isinstance(s, dict) else None)
        for s in (opp.get("services_recommended") or [])
    ]
    sa = expected.get("services_recommended_any")
    if sa and not (set(services) & set(sa)):
        failures.append(f"services_recommended_any: got {services}, wanted any of {sa}")

    tier = score.get("tier")
    ti = expected.get("tier_in")
    if ti and tier not in ti:
        failures.append(f"tier_in: got '{tier}', expected one of {ti}")

    smin = expected.get("score_min")
    if smin is not None:
        total = score.get("total")
        if not isinstance(total, (int, float)) or total < smin:
            failures.append(f"score_min: got {total}, want >= {smin}")

    return failures


async def run_eval(cases: list[GoldenCase]) -> EvalReport:
    started = dt.datetime.utcnow().isoformat()
    records: list[EvalRecord] = []
    total_cost = 0
    n_passed = 0
    for i, c in enumerate(cases, start=1):
        try:
            out = await run_research(
                brand_id=-i,  # synthetic negative id keeps DB rows untouched in eval mode
                brand_name=c.name,
                brand_domain=c.domain,
                reason="eval",
            )
        except Exception as exc:  # noqa: BLE001
            records.append(
                EvalRecord(
                    name=c.name,
                    passed=False,
                    failures=[f"exception: {type(exc).__name__}: {exc}"],
                    score_total=None,
                    tier=None,
                    cost_cents=0,
                )
            )
            continue
        failures = _check(c.expected, out)
        passed = len(failures) == 0
        if passed:
            n_passed += 1
        total_cost += out.total_cost_cents
        records.append(
            EvalRecord(
                name=c.name,
                passed=passed,
                failures=failures,
                score_total=(out.score or {}).get("total") if out.score else None,
                tier=(out.score or {}).get("tier") if out.score else None,
                cost_cents=out.total_cost_cents,
            )
        )

    return EvalReport(
        started_at=started,
        finished_at=dt.datetime.utcnow().isoformat(),
        n_cases=len(cases),
        n_passed=n_passed,
        pass_rate=(n_passed / len(cases)) if cases else 0.0,
        total_cost_cents=total_cost,
        records=records,
    )
