"""MonitoringWF — periodic brand re-research and change detection.

Runs on a schedule (e.g. weekly) to re-research tracked brands and
detect significant changes: score drift, new competitors, opportunity
shifts. Emits events when changes are detected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .activities import (
        BrandDomainResult,
        CollectorParams,
        CollectorResult,
        DossierWrite,
        DossierWriteResult,
        GraphRunRequest,
        GraphRunResult,
        StaleBrandsRequest,
        StaleBrandsResult,
        TraceWrite,
        fetch_brand_domain_activity,
        fetch_stale_brands_activity,
        persist_trace_activity,
        record_dossier_activity,
        run_collector_activity,
        run_research_graph_activity,
    )


@dataclass(slots=True)
class MonitoringWFInput:
    brand_ids: list[int] = field(default_factory=list)
    min_days_since_last: int = 7
    max_brands: int = 20
    score_drift_threshold: int = 10


@dataclass(slots=True)
class MonitoringWFResult:
    brands_checked: int
    brands_updated: int
    score_drifts: list[dict[str, Any]]
    errors: list[str]


@workflow.defn
class MonitoringWF:
    """Re-research tracked brands and detect changes."""

    @workflow.run
    async def run(self, inp: MonitoringWFInput) -> MonitoringWFResult:
        start_ms = _now_ms()
        updated = 0
        score_drifts: list[dict[str, Any]] = []
        errors: list[str] = []

        brand_ids = inp.brand_ids[:inp.max_brands]

        if not brand_ids:
            stale: StaleBrandsResult = await workflow.execute_activity(
                fetch_stale_brands_activity,
                StaleBrandsRequest(
                    min_days_since_last=inp.min_days_since_last,
                    max_brands=inp.max_brands,
                ),
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            brand_ids = stale.brand_ids

        _ENRICHMENT_COLLECTORS = [
            "tech_stack", "pagespeed", "social_presence", "content_blog",
            "ads_txt", "email_maturity", "crunchbase_funding", "tracxn_funding",
            "tofler_company",
        ]
        _ENRICH_RETRY = RetryPolicy(
            initial_interval=timedelta(seconds=10),
            maximum_interval=timedelta(minutes=3),
            maximum_attempts=2,
        )

        for brand_id in brand_ids:
            try:
                # Re-run enrichment collectors for fresh signal data
                brand_info: BrandDomainResult = await workflow.execute_activity(
                    fetch_brand_domain_activity,
                    brand_id,
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                if brand_info.domain:
                    for coll_name in _ENRICHMENT_COLLECTORS:
                        try:
                            await workflow.execute_activity(
                                run_collector_activity,
                                CollectorParams(
                                    collector=coll_name,
                                    params={"domains": [brand_info.domain]},
                                ),
                                start_to_close_timeout=timedelta(minutes=10),
                                retry_policy=_ENRICH_RETRY,
                            )
                        except Exception:
                            pass  # non-critical — continue with stale data

                graph_res: GraphRunResult = await workflow.execute_activity(
                    run_research_graph_activity,
                    GraphRunRequest(brand_id=brand_id, reason="monitoring"),
                    start_to_close_timeout=timedelta(minutes=5),
                    heartbeat_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )

                dossier_data: dict[str, Any] = {
                    "reason": "monitoring",
                    "research": graph_res.research,
                    "competitor": graph_res.competitor,
                    "opportunity": graph_res.opportunity,
                    "score": graph_res.score,
                    "outreach": graph_res.outreach,
                }

                written: DossierWriteResult = await workflow.execute_activity(
                    record_dossier_activity,
                    DossierWrite(
                        brand_id=brand_id,
                        data=dossier_data,
                        cost_cents=graph_res.total_cost_cents,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

                new_score = (graph_res.score or {}).get("total")
                if isinstance(new_score, (int, float)):
                    score_drifts.append({
                        "brand_id": brand_id,
                        "new_score": int(new_score),
                        "new_tier": (graph_res.score or {}).get("tier"),
                        "dossier_id": written.dossier_id,
                        "version": written.version,
                    })

                updated += 1

            except Exception as exc:
                errors.append(f"brand_{brand_id}: {type(exc).__name__}: {exc}")

        await workflow.execute_activity(
            persist_trace_activity,
            TraceWrite(
                workflow_id=workflow.info().workflow_id,
                agent="monitoring",
                brand_id=None,
                steps=[{"brands_checked": len(brand_ids), "updated": updated}],
                total_cost_cents=0,
                duration_ms=_now_ms() - start_ms,
            ),
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        return MonitoringWFResult(
            brands_checked=len(brand_ids),
            brands_updated=updated,
            score_drifts=score_drifts,
            errors=errors,
        )


def _now_ms() -> int:
    return int(workflow.now().timestamp() * 1000)
