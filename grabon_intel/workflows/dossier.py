"""DossierWF — uses the LangGraph supervisor.

The workflow itself is small now:
  1. run the research graph (single activity — LangGraph internals stay inside)
  2. persist the dossier
  3. persist the trace
  4. open an approval if score tier ∈ {hot, warm}

Why one activity for the graph and not one-activity-per-node:
  - Cross-node state lives in memory inside the supervisor; passing it
    through Temporal between five activities would mean serialising large
    intermediate payloads (research+competitor+opportunity) every step.
  - The graph is short-lived (<2 min typical). Activity heartbeat + retry
    is enough resilience for now. If a single node becomes long-running
    we'll promote it to its own activity later.
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
        CreateApprovalRequest,
        DossierWrite,
        DossierWriteResult,
        GraphRunRequest,
        GraphRunResult,
        TraceWrite,
        create_approval_activity,
        fetch_brand_domain_activity,
        persist_trace_activity,
        record_dossier_activity,
        run_collector_activity,
        run_research_graph_activity,
    )


_GRAPH_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(seconds=120),
    backoff_coefficient=2.0,
    maximum_attempts=5,
    non_retryable_error_types=["grabon_intel.llm.router.BudgetGateError"],
)


@dataclass(slots=True)
class DossierWFInput:
    brand_id: int
    reason: str = "manual"
    brand_hint: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DossierWFResult:
    brand_id: int
    dossier_id: int
    version: int
    total_cost_cents: int
    nodes: list[str]
    score_total: int | None
    tier: str | None


@workflow.defn
class DossierWF:
    @workflow.run
    async def run(self, inp: DossierWFInput) -> DossierWFResult:
        start_ms = _now_ms()

        # Resolve brand domain/name if not provided
        brand_domain = inp.brand_hint.get("domain")
        brand_name = inp.brand_hint.get("name")
        if not brand_domain:
            brand_info: BrandDomainResult = await workflow.execute_activity(
                fetch_brand_domain_activity,
                inp.brand_id,
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            brand_domain = brand_info.domain
            brand_name = brand_name or brand_info.name

        # Run enrichment collectors in parallel if brand has a domain
        if brand_domain:
            _ENRICH_RETRY = RetryPolicy(
                initial_interval=timedelta(seconds=5),
                maximum_interval=timedelta(minutes=2),
                maximum_attempts=2,
            )

            # Vane research first (slowest, provides AI synthesis for other nodes)
            try:
                vane_params: dict[str, Any] = {"domains": [brand_domain]}
                if brand_name:
                    vane_params["brand_names"] = [brand_name]
                await workflow.execute_activity(
                    run_collector_activity,
                    CollectorParams(collector="vane_research", params=vane_params),
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=_ENRICH_RETRY,
                )
            except Exception as exc:
                workflow.logger.warning(
                    "vane_research failed for brand %s (%s): %s",
                    inp.brand_id, brand_domain, exc,
                )

            # Remaining collectors run in parallel (Temporal coroutines)
            _PARALLEL_COLLECTORS = [
                "tech_stack", "pagespeed", "social_presence", "content_blog",
                "ads_txt", "email_maturity", "contact_enrichment",
            ]
            coros = []
            for coll_name in _PARALLEL_COLLECTORS:
                params: dict[str, Any] = {"domains": [brand_domain]}
                if coll_name == "contact_enrichment" and brand_name:
                    params["brand_names"] = [brand_name]
                coros.append(
                    workflow.execute_activity(
                        run_collector_activity,
                        CollectorParams(collector=coll_name, params=params),
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=_ENRICH_RETRY,
                    )
                )
            # asyncio.gather works in Temporal workflows for concurrent activities
            import asyncio as _asyncio
            settled = await _asyncio.gather(*coros, return_exceptions=True)
            del settled  # results don't matter — signals stored in DB by collectors

        graph_res: GraphRunResult = await workflow.execute_activity(
            run_research_graph_activity,
            GraphRunRequest(
                brand_id=inp.brand_id,
                brand_name=brand_name,
                brand_domain=brand_domain,
                reason=inp.reason,
            ),
            start_to_close_timeout=timedelta(minutes=10),
            heartbeat_timeout=timedelta(minutes=3),
            retry_policy=_GRAPH_RETRY,
        )

        dossier_data: dict[str, Any] = {
            "reason": inp.reason,
            "research": graph_res.research,
            "competitor": graph_res.competitor,
            "opportunity": graph_res.opportunity,
            "score": graph_res.score,
            "outreach": graph_res.outreach,
            "tool_data_keys": sorted(graph_res.tool_data.keys()),
            "signal_evidence": graph_res.signal_data,
        }

        written: DossierWriteResult = await workflow.execute_activity(
            record_dossier_activity,
            DossierWrite(
                brand_id=inp.brand_id,
                data=dossier_data,
                cost_cents=graph_res.total_cost_cents,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        await workflow.execute_activity(
            persist_trace_activity,
            TraceWrite(
                workflow_id=workflow.info().workflow_id,
                agent="dossier",
                brand_id=inp.brand_id,
                steps=graph_res.nodes,
                total_cost_cents=graph_res.total_cost_cents,
                duration_ms=_now_ms() - start_ms,
            ),
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        score = graph_res.score or {}
        total = score.get("total")
        tier = score.get("tier")

        # Auto-create approval for hot/warm tier leads
        if isinstance(tier, str) and tier in ("hot", "warm"):
            try:
                await workflow.execute_activity(
                    create_approval_activity,
                    CreateApprovalRequest(
                        brand_id=inp.brand_id,
                        dossier_id=written.dossier_id,
                        tier=tier,
                    ),
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            except Exception:
                pass  # non-critical — dossier already persisted

        return DossierWFResult(
            brand_id=inp.brand_id,
            dossier_id=written.dossier_id,
            version=written.version,
            total_cost_cents=graph_res.total_cost_cents,
            nodes=[n.get("node", "?") for n in graph_res.nodes],
            score_total=int(total) if isinstance(total, (int, float)) else None,
            tier=tier if isinstance(tier, str) else None,
        )


def _now_ms() -> int:
    return int(workflow.now().timestamp() * 1000)
