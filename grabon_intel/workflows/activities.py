"""Temporal activities.

Activities are *the only* place that may do I/O. Workflows must stay
deterministic — they call into these via `workflow.execute_activity`.
Each activity is idempotent where reasonable; Temporal handles retries.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy import text as sa_text  # alias kept for clarity in graph activity
from temporalio import activity

from ..db import session as session_ctx
from ..graph import run_research
from ..llm import LLMResult, Tier, complete
from ..logging import get_logger
from ..resolver import resolve_or_create
from ..signals.base import run_collector
from ..signals.ads_txt import AdsTxtCollector
from ..signals.affiliate_program import AffiliateProgramCollector
from ..signals.app_store import AppStoreCollector
from ..signals.cert_transparency import CertTransparencyCollector
from ..signals.common_crawl import CommonCrawlCollector
from ..signals.content_blog import ContentBlogCollector
from ..signals.email_maturity import EmailMaturityCollector
from ..signals.google_ads_transparency import GoogleAdsTransparencyCollector
from ..signals.google_trends import GoogleTrendsCollector
from ..signals.hn_discovery import HNDiscoveryCollector
from ..signals.ios_app_store import IOSAppStoreCollector
from ..signals.ipinfo_geo import IPInfoGeoCollector
from ..signals.linkedin_jobs import LinkedInJobsCollector
from ..signals.meta_ad_library import MetaAdLibraryCollector
from ..signals.news_polling import NewsPollingCollector
from ..signals.open_food_facts import OpenFoodFactsCollector
from ..signals.pagespeed import PageSpeedCollector
from ..signals.rdap_whois import RDAPWhoisCollector
from ..signals.seo_rank_tracking import SEORankTrackingCollector
from ..signals.shodan_internetdb import ShodanInternetDBCollector
from ..signals.social_presence import SocialPresenceCollector
from ..signals.tech_stack import TechStackCollector
from ..signals.urlscan_search import URLScanSearchCollector
from ..signals.youtube_social import YouTubeSocialCollector
from ..signals.crunchbase_funding import CrunchbaseFundingCollector
from ..signals.tofler_company import ToflerCompanyCollector
from ..signals.tracxn_funding import TracxnFundingCollector
from ..signals.vane_discovery import VaneDiscoveryCollector
from ..signals.vane_research import VaneResearchCollector
from ..signals.contact_enrichment import ContactEnrichmentCollector

log = get_logger(__name__)


# --- Inputs / outputs as plain dataclasses (Temporal serialises via JSON) -----


@dataclass(slots=True)
class CollectorParams:
    collector: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CollectorResult:
    collector: str
    emitted: int
    deduped: int


@dataclass(slots=True)
class BrandHint:
    name: str | None = None
    domain: str | None = None


@dataclass(slots=True)
class ResolvedBrand:
    brand_id: int | None
    name: str | None
    domain: str | None


@dataclass(slots=True)
class FanOutResult:
    brand_ids: list[int]


@dataclass(slots=True)
class LLMCallRequest:
    tier: str = Tier.FAST.value
    system: str | None = None
    prompt: str = ""
    max_output_tokens: int = 800
    temperature: float = 0.2
    json_mode: bool = False


@dataclass(slots=True)
class LLMCallResult:
    content: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_cents: int


@dataclass(slots=True)
class DossierWrite:
    brand_id: int
    data: dict[str, Any]
    markdown: str | None = None
    cost_cents: int = 0


@dataclass(slots=True)
class DossierWriteResult:
    dossier_id: int
    version: int


@dataclass(slots=True)
class GraphRunRequest:
    brand_id: int
    brand_name: str | None = None
    brand_domain: str | None = None
    reason: str = "manual"


@dataclass(slots=True)
class GraphRunResult:
    brand_id: int
    research: dict | None
    competitor: dict | None
    opportunity: dict | None
    score: dict | None
    outreach: dict | None
    nodes: list[dict[str, Any]]
    total_cost_cents: int
    tool_data: dict[str, Any]
    signal_data: dict[str, Any] | None = None


@dataclass(slots=True)
class TraceWrite:
    workflow_id: str
    agent: str
    brand_id: int | None
    steps: list[dict[str, Any]]
    total_cost_cents: int
    duration_ms: int | None
    status: str = "ok"
    error: str | None = None


# --- Collector registry -------------------------------------------------------


_COLLECTORS = {
    "meta_ad_library": MetaAdLibraryCollector,
    "linkedin_jobs": LinkedInJobsCollector,
    "news_polling": NewsPollingCollector,
    "google_ads_transparency": GoogleAdsTransparencyCollector,
    "seo_rank_tracking": SEORankTrackingCollector,
    "youtube_social": YouTubeSocialCollector,
    "app_store": AppStoreCollector,
    "ios_app_store": IOSAppStoreCollector,
    "ads_txt": AdsTxtCollector,
    "email_maturity": EmailMaturityCollector,
    "tech_stack": TechStackCollector,
    "pagespeed": PageSpeedCollector,
    "google_trends": GoogleTrendsCollector,
    "cert_transparency": CertTransparencyCollector,
    "rdap_whois": RDAPWhoisCollector,
    "common_crawl": CommonCrawlCollector,
    "shodan_internetdb": ShodanInternetDBCollector,
    "hn_discovery": HNDiscoveryCollector,
    "open_food_facts": OpenFoodFactsCollector,
    "urlscan_search": URLScanSearchCollector,
    "ipinfo_geo": IPInfoGeoCollector,
    "affiliate_program": AffiliateProgramCollector,
    "social_presence": SocialPresenceCollector,
    "content_blog": ContentBlogCollector,
    "crunchbase_funding": CrunchbaseFundingCollector,
    "tracxn_funding": TracxnFundingCollector,
    "tofler_company": ToflerCompanyCollector,
    "vane_research": VaneResearchCollector,
    "vane_discovery": VaneDiscoveryCollector,
    "contact_enrichment": ContactEnrichmentCollector,
}


def _build_collector(name: str, params: dict[str, Any]):
    klass = _COLLECTORS.get(name)
    if klass is None:
        raise ValueError(f"unknown collector: {name}")
    from ..config import get_settings
    settings = get_settings()
    if name == "pagespeed" and "api_key" not in params:
        if settings.google_pagespeed_api_key:
            params = {**params, "api_key": settings.google_pagespeed_api_key}
    if name == "urlscan_search" and "api_key" not in params:
        if settings.urlscan_api_key:
            params = {**params, "api_key": settings.urlscan_api_key}
    if name == "ipinfo_geo" and "api_token" not in params:
        if settings.ipinfo_token:
            params = {**params, "api_token": settings.ipinfo_token}
    return klass(**params)


# --- Activities ---------------------------------------------------------------


@activity.defn(name="run_collector")
async def run_collector_activity(p: CollectorParams) -> CollectorResult:
    from ..logging import ErrorCategory, classify_error, record_error

    log.info("activity.run_collector", collector=p.collector, params=p.params)
    try:
        c = _build_collector(p.collector, p.params)
    except ValueError as exc:
        record_error(
            category=ErrorCategory.CONFIG_ERROR,
            source=f"activity.run_collector",
            message=f"Unknown collector: {p.collector}",
            detail=str(p.params),
        )
        raise

    try:
        counts = await run_collector(c)
    except Exception as exc:
        cat = classify_error(exc)
        record_error(
            category=cat,
            source=f"activity.{p.collector}",
            message=f"{type(exc).__name__}: {exc}"[:300],
            detail=str(p.params),
        )
        raise

    if counts["emitted"] == 0 and p.collector == "meta_ad_library":
        from ..config import get_settings
        token = get_settings().meta_ad_library_token.get_secret_value()
        if not token:
            search = p.params.get("search_terms") or p.params.get("industry") or ""
            if search:
                log.info("activity.fallback_to_google_ads", search=search)
                fb = GoogleAdsTransparencyCollector(search_terms=search, region=p.params.get("country", "IN"))
                fb_counts = await run_collector(fb)
                return CollectorResult(
                    collector="google_ads_transparency",
                    emitted=fb_counts["emitted"],
                    deduped=fb_counts["deduped"],
                )

    log.info("activity.run_collector.done", collector=p.collector, emitted=counts["emitted"], deduped=counts["deduped"])
    return CollectorResult(collector=p.collector, emitted=counts["emitted"], deduped=counts["deduped"])


@activity.defn(name="resolve_brand")
async def resolve_brand_activity(hint: BrandHint) -> ResolvedBrand:
    async with session_ctx() as s:
        bid = await resolve_or_create(s, name=hint.name, domain=hint.domain)
    return ResolvedBrand(brand_id=bid, name=hint.name, domain=hint.domain)


@dataclass(slots=True)
class EnrichHintsRequest:
    since_minutes: int = 360
    max_hints: int = 20


@dataclass(slots=True)
class EnrichHintsResult:
    resolved: int
    failed: int


@activity.defn(name="enrich_unresolved_hints")
async def enrich_unresolved_hints_activity(req: EnrichHintsRequest) -> EnrichHintsResult:
    """Resolve domain-less signal hints via SearXNG search → create real brands."""
    from ..tools.searxng import SearXNGTool

    search = SearXNGTool()
    resolved = 0
    failed = 0

    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT DISTINCT brand_hint FROM signals "
                    "WHERE brand_id IS NULL "
                    "AND brand_hint IS NOT NULL AND brand_hint != '' "
                    "AND ingested_at > NOW() - make_interval(mins => :m) "
                    "LIMIT :lim"
                ),
                {"m": req.since_minutes, "lim": req.max_hints},
            )
        ).all()

    for row in rows:
        hint = row[0]
        try:
            r = await search.safe(query=f"{hint} official website India")
            if r.degraded:
                failed += 1
                continue
            domain = _extract_domain_from_serp(r.data, hint)
            if not domain:
                failed += 1
                continue
            async with session_ctx() as s:
                bid = await resolve_or_create(s, name=hint, domain=domain)
                if bid:
                    await s.execute(
                        text(
                            "UPDATE signals SET brand_id = :bid "
                            "WHERE brand_id IS NULL AND brand_hint = :hint"
                        ),
                        {"bid": bid, "hint": hint},
                    )
                    resolved += 1
                else:
                    failed += 1
        except Exception as exc:
            from ..logging import classify_error, record_error
            cat = classify_error(exc)
            log.warning("enrich_hint.error", hint=hint, category=cat.value, error=str(exc))
            record_error(
                category=cat,
                source="activity.enrich_hints",
                message=f"Failed to enrich hint '{hint}': {exc}"[:300],
            )
            failed += 1

    return EnrichHintsResult(resolved=resolved, failed=failed)


def _extract_domain_from_serp(data: dict | None, hint: str) -> str | None:
    """Extract the most likely official domain from search results."""
    import re
    from urllib.parse import urlparse

    if not data:
        return None
    results = data.get("results") or data.get("items") or []
    hint_lower = hint.lower().replace(" ", "")

    for item in results[:5]:
        url = item.get("url") or item.get("link") or ""
        if not url:
            continue
        parsed = urlparse(url)
        host = (parsed.hostname or "").removeprefix("www.").lower()
        if not host or "." not in host:
            continue
        # Skip aggregators/news/social
        skip = ("wikipedia.", "linkedin.", "facebook.", "instagram.", "twitter.",
                "youtube.", "crunchbase.", "tracxn.", "zoominfo.", "glassdoor.",
                "ambitionbox.", "news.", "economictimes.", "livemint.")
        if any(s in host for s in skip):
            continue
        # Prefer domains that resemble the brand name
        host_clean = re.sub(r"[^a-z0-9]", "", host.split(".")[0])
        if hint_lower[:4] in host_clean or host_clean in hint_lower:
            return host
    # Fallback: first non-aggregator result
    for item in results[:3]:
        url = item.get("url") or item.get("link") or ""
        if not url:
            continue
        parsed = urlparse(url)
        host = (parsed.hostname or "").removeprefix("www.").lower()
        if not host or "." not in host:
            continue
        skip = ("wikipedia.", "linkedin.", "facebook.", "instagram.", "twitter.",
                "youtube.", "crunchbase.", "tracxn.", "zoominfo.", "glassdoor.",
                "ambitionbox.", "news.", "economictimes.", "livemint.")
        if any(s in host for s in skip):
            continue
        return host
    return None


@activity.defn(name="fan_out_new_brands")
async def fan_out_new_brands_activity(since_minutes: int) -> FanOutResult:
    """Return brand_ids with ≥2 distinct signal types and no existing dossier.

    Multi-signal threshold prevents single-noise brands from burning LLM budget.
    """
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT s.brand_id "
                    "FROM signals s "
                    "JOIN brands b ON b.id = s.brand_id "
                    "WHERE s.brand_id IS NOT NULL "
                    "AND s.ingested_at > NOW() - make_interval(mins => :m) "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM dossiers d WHERE d.brand_id = s.brand_id"
                    ") "
                    "GROUP BY s.brand_id "
                    "HAVING COUNT(DISTINCT split_part(s.type, '.', 1)) >= 2 "
                    "ORDER BY COUNT(DISTINCT s.type) DESC, s.brand_id"
                ),
                {"m": since_minutes},
            )
        ).all()
    return FanOutResult(brand_ids=[int(r[0]) for r in rows])


@activity.defn(name="llm_complete")
async def llm_complete_activity(req: LLMCallRequest) -> LLMCallResult:
    res: LLMResult = await complete(
        tier=Tier(req.tier),
        system=req.system,
        prompt=req.prompt,
        max_output_tokens=req.max_output_tokens,
        temperature=req.temperature,
        json_mode=req.json_mode,
    )
    return LLMCallResult(
        content=res.content,
        model_id=res.model_id,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
        cost_cents=res.cost_cents,
    )


async def _fetch_signal_data(brand_id: int) -> dict[str, Any]:
    """Fetch latest signal data from collectors for this brand, grouped by source."""
    async with session_ctx() as s:
        rows = (
            await s.execute(
                sa_text(
                    "SELECT DISTINCT ON (source, type, COALESCE(value_text, '')) "
                    "source, type, value_num, value_text, payload, observed_at "
                    "FROM signals WHERE brand_id = :bid "
                    "ORDER BY source, type, COALESCE(value_text, ''), observed_at DESC"
                ),
                {"bid": brand_id},
            )
        ).mappings().all()
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        src = r["source"]
        entry: dict[str, Any] = {
            "type": r["type"],
            "value_num": float(r["value_num"]) if r["value_num"] is not None else None,
            "value_text": r["value_text"],
            "observed_at": r["observed_at"].isoformat() if r["observed_at"] else None,
        }
        if r["payload"]:
            entry["payload"] = r["payload"]
        grouped.setdefault(src, []).append(entry)
    return grouped


@activity.defn(name="run_research_graph")
async def run_research_graph_activity(req: GraphRunRequest) -> GraphRunResult:
    """Run the LangGraph supervisor for one brand. Pure I/O activity."""
    from ..graph.supervisor import run_research_streaming

    log.info("activity.run_research_graph", brand_id=req.brand_id)
    if not req.brand_domain or not req.brand_name:
        async with session_ctx() as s:
            row = (
                await s.execute(
                    sa_text(
                        "SELECT name, domain FROM brands WHERE id = :id"
                    ),
                    {"id": req.brand_id},
                )
            ).first()
            if row:
                req.brand_name = req.brand_name or row[0]
                req.brand_domain = req.brand_domain or row[1]

    signal_data = await _fetch_signal_data(req.brand_id)

    out = None
    async for node_name, node_data in run_research_streaming(
        brand_id=req.brand_id,
        brand_name=req.brand_name,
        brand_domain=req.brand_domain,
        reason=req.reason,
        signal_data=signal_data,
    ):
        if node_name == "__done__":
            out = node_data["result"]
        else:
            activity.heartbeat(f"node:{node_name}")
            log.info("activity.graph_node_done", brand_id=req.brand_id, node=node_name)

    if out is None:
        raise RuntimeError("research graph returned no result")

    return GraphRunResult(
        brand_id=out.brand_id,
        research=out.research,
        competitor=out.competitor,
        opportunity=out.opportunity,
        score=out.score,
        outreach=out.outreach,
        nodes=[dict(n) for n in out.nodes],
        total_cost_cents=out.total_cost_cents,
        tool_data=out.tool_data,
        signal_data=out.signal_data,
    )


@activity.defn(name="record_dossier")
async def record_dossier_activity(w: DossierWrite) -> DossierWriteResult:
    """Insert a new dossier version. Version = max(existing)+1, atomic."""
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "WITH next AS ("
                    "  SELECT COALESCE(MAX(version), 0) + 1 AS v FROM dossiers WHERE brand_id = :b"
                    ") "
                    "INSERT INTO dossiers (brand_id, version, data, markdown, cost_cents) "
                    "SELECT :b, next.v, CAST(:d AS JSONB), :m, :c FROM next "
                    "RETURNING id, version"
                ),
                {
                    "b": w.brand_id,
                    "d": _json(w.data),
                    "m": w.markdown,
                    "c": w.cost_cents,
                },
            )
        ).first()
    return DossierWriteResult(dossier_id=int(row[0]), version=int(row[1]))


@activity.defn(name="persist_trace")
async def persist_trace_activity(t: TraceWrite) -> None:
    async with session_ctx() as s:
        await s.execute(
            text(
                "INSERT INTO agent_traces "
                "(workflow_id, brand_id, agent, steps, total_cost_cents, duration_ms, status, error) "
                "VALUES (:wf, :bid, :ag, CAST(:st AS JSONB), :cc, :dur, :stt, :err)"
            ),
            {
                "wf": t.workflow_id,
                "bid": t.brand_id,
                "ag": t.agent,
                "st": _json(t.steps),
                "cc": t.total_cost_cents,
                "dur": t.duration_ms,
                "stt": t.status,
                "err": t.error,
            },
        )


@dataclass(slots=True)
class StaleBrandsRequest:
    min_days_since_last: int = 7
    max_brands: int = 20


@dataclass(slots=True)
class StaleBrandsResult:
    brand_ids: list[int]


@activity.defn(name="fetch_stale_brands")
async def fetch_stale_brands_activity(req: StaleBrandsRequest) -> StaleBrandsResult:
    """Find brands with real domains whose last dossier is older than threshold."""
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT b.id FROM brands b "
                    "LEFT JOIN LATERAL ("
                    "  SELECT generated_at FROM dossiers WHERE brand_id = b.id "
                    "  ORDER BY version DESC LIMIT 1"
                    ") d ON true "
                    "WHERE b.domain IS NOT NULL AND b.domain != '' "
                    "AND (d.generated_at IS NULL OR d.generated_at < NOW() - make_interval(days => :days)) "
                    "ORDER BY d.generated_at ASC NULLS FIRST "
                    "LIMIT :lim"
                ),
                {"days": req.min_days_since_last, "lim": req.max_brands},
            )
        ).all()
    return StaleBrandsResult(brand_ids=[int(r[0]) for r in rows])


@dataclass(slots=True)
class RecentDomainsRequest:
    since_minutes: int = 360
    max_domains: int = 20


@dataclass(slots=True)
class RecentDomainsResult:
    domains: list[str]


@activity.defn(name="fetch_recent_brand_domains")
async def fetch_recent_brand_domains_activity(req: RecentDomainsRequest) -> RecentDomainsResult:
    """Fetch domains of recently discovered brands for enrichment collectors."""
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT DISTINCT b.domain FROM brands b "
                    "JOIN signals s ON s.brand_id = b.id "
                    "WHERE b.domain IS NOT NULL AND b.domain != '' "
                    "AND s.ingested_at > NOW() - make_interval(mins => :m) "
                    "ORDER BY b.domain "
                    "LIMIT :lim"
                ),
                {"m": req.since_minutes, "lim": req.max_domains},
            )
        ).all()
    return RecentDomainsResult(domains=[str(r[0]) for r in rows])


async def compute_quick_score(brand_id: int) -> dict[str, Any]:
    """Lightweight score from existing signals — no LLM calls."""
    async with session_ctx() as s:
        rows = (
            await s.execute(
                sa_text(
                    "SELECT source, type, value_num, value_text "
                    "FROM signals WHERE brand_id = :bid"
                ),
                {"bid": brand_id},
            )
        ).mappings().all()

    score = 0
    sources: set[str] = set()
    signals_found: list[str] = []

    for r in rows:
        src = r["source"]
        sig_type = r["type"] or ""
        sources.add(src)

        if "funding" in sig_type or src in ("crunchbase_funding", "tracxn_funding"):
            score += 20
            signals_found.append("funding")
        elif "revenue" in sig_type:
            score += 15
            signals_found.append("revenue")
        elif src == "linkedin_jobs" or "hiring" in sig_type:
            score += 10
            signals_found.append("hiring")
        elif "ad_spend" in sig_type or src in ("meta_ad_library", "google_ads_transparency"):
            score += 10
            signals_found.append("paid_ads")
        elif src == "tech_stack":
            score += 5
            signals_found.append("tech_stack")
        elif src == "social_presence" or "social" in sig_type:
            score += 5
            signals_found.append("social")
        elif src == "seo_rank_tracking" or "seo" in sig_type:
            score += 5
            signals_found.append("seo")
        elif src == "pagespeed":
            score += 3
            signals_found.append("pagespeed")
        elif src == "vane_research":
            score += 15
            signals_found.append("ai_research")

    return {
        "brand_id": brand_id,
        "quick_score": score,
        "signals_found": list(set(signals_found)),
        "source_count": len(sources),
        "pass": score >= 25,
    }


@dataclass(slots=True)
class PreQualifyRequest:
    brand_ids: list[int]


@dataclass(slots=True)
class PreQualifyResult:
    qualified: list[int]
    rejected: list[dict[str, Any]]


@activity.defn(name="pre_qualify_brands")
async def pre_qualify_brands_activity(req: PreQualifyRequest) -> PreQualifyResult:
    qualified: list[int] = []
    rejected: list[dict[str, Any]] = []
    for bid in req.brand_ids:
        result = await compute_quick_score(bid)
        if result["pass"]:
            qualified.append(bid)
        else:
            rejected.append(result)
            log.info("pre_qualify.rejected", brand_id=bid, score=result["quick_score"],
                     signals=result["signals_found"])
    return PreQualifyResult(qualified=qualified, rejected=rejected)


@dataclass(slots=True)
class BrandDomainResult:
    brand_id: int
    name: str | None
    domain: str | None


@activity.defn(name="fetch_brand_domain")
async def fetch_brand_domain_activity(brand_id: int) -> BrandDomainResult:
    async with session_ctx() as s:
        row = (
            await s.execute(
                sa_text("SELECT name, domain FROM brands WHERE id = :id"),
                {"id": brand_id},
            )
        ).first()
    if row:
        return BrandDomainResult(brand_id=brand_id, name=row[0], domain=row[1])
    return BrandDomainResult(brand_id=brand_id, name=None, domain=None)


@dataclass(slots=True)
class CreateApprovalRequest:
    brand_id: int
    dossier_id: int
    tier: str


@activity.defn(name="create_approval")
async def create_approval_activity(req: CreateApprovalRequest) -> int:
    async with session_ctx() as s:
        row = (
            await s.execute(
                sa_text(
                    "INSERT INTO approvals (entity_type, entity_id, brand_id, status, notes) "
                    "VALUES ('dossier', :did, :bid, 'pending', :notes) "
                    "RETURNING id"
                ),
                {
                    "did": req.dossier_id,
                    "bid": req.brand_id,
                    "notes": f"Auto-flagged: {req.tier} tier lead",
                },
            )
        ).first()
    return int(row[0])


def _json(obj: Any) -> str:
    import orjson

    return orjson.dumps(obj, default=str).decode("utf-8")


# Helpful for callers
__all__ = [
    "CollectorParams",
    "CollectorResult",
    "BrandHint",
    "ResolvedBrand",
    "FanOutResult",
    "LLMCallRequest",
    "LLMCallResult",
    "GraphRunRequest",
    "GraphRunResult",
    "DossierWrite",
    "DossierWriteResult",
    "TraceWrite",
    "StaleBrandsRequest",
    "StaleBrandsResult",
    "run_collector_activity",
    "resolve_brand_activity",
    "fan_out_new_brands_activity",
    "llm_complete_activity",
    "run_research_graph_activity",
    "record_dossier_activity",
    "persist_trace_activity",
    "fetch_stale_brands_activity",
    "RecentDomainsRequest",
    "RecentDomainsResult",
    "fetch_recent_brand_domains_activity",
    "PreQualifyRequest",
    "PreQualifyResult",
    "pre_qualify_brands_activity",
    "BrandDomainResult",
    "fetch_brand_domain_activity",
    "CreateApprovalRequest",
    "create_approval_activity",
]


# Keep imports referenced (asdict + dt used by callers / future code paths)
_ = (asdict, dt)
