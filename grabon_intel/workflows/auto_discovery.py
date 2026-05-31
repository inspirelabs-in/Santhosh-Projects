"""AutoDiscoveryWF: autonomous lead-finding loop.

Runs on a Temporal schedule (default every 6h). Iterates ICP seed queries
across available collectors, then fans out DossierWF for newly discovered
brands. Designed to be the 24/7 autonomous heartbeat of the system.

Collectors used (no API keys needed):
  - news_polling: Google News RSS via SearXNG
  - google_ads_transparency: public Google Ads scraper
  - linkedin_jobs: hiring signals from LinkedIn job posts
  - meta_ad_library: active ad-buying brands (Meta/Facebook)
  - seo_rank_tracking: SERP position tracking via SearXNG
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.workflow import ParentClosePolicy

with workflow.unsafe.imports_passed_through():
    from .activities import (
        CollectorParams,
        CollectorResult,
        EnrichHintsRequest,
        EnrichHintsResult,
        FanOutResult,
        PreQualifyRequest,
        PreQualifyResult,
        RecentDomainsRequest,
        RecentDomainsResult,
        enrich_unresolved_hints_activity,
        fan_out_new_brands_activity,
        fetch_recent_brand_domains_activity,
        pre_qualify_brands_activity,
        run_collector_activity,
    )
    from .dossier import DossierWF, DossierWFInput


_COLLECTOR_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(minutes=3),
    maximum_attempts=3,
    non_retryable_error_types=["ValueError"],
)


@dataclass(slots=True)
class AutoDiscoveryWFInput:
    queries: list[str] = field(default_factory=list)
    geos: list[str] = field(default_factory=lambda: ["IN"])
    max_fan_out: int = 5
    fan_out_since_minutes: int = 360


@dataclass(slots=True)
class AutoDiscoveryWFResult:
    cycles: list[dict[str, Any]]
    total_signals: int
    fanned_out_brand_ids: list[int]


@workflow.defn
class AutoDiscoveryWF:
    @workflow.run
    async def run(self, inp: AutoDiscoveryWFInput) -> AutoDiscoveryWFResult:
        queries = inp.queries
        geos = inp.geos or ["IN"]
        cycles: list[dict[str, Any]] = []
        total_signals = 0

        # Phase 1: Run news_polling with all queries per geo
        for geo in geos:
            try:
                result: CollectorResult = await workflow.execute_activity(
                    run_collector_activity,
                    CollectorParams(
                        collector="news_polling",
                        params={"queries": queries, "country": geo, "max_items_per_query": 10},
                    ),
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=_COLLECTOR_RETRY,
                )
                cycles.append({
                    "collector": "news_polling",
                    "geo": geo,
                    "emitted": result.emitted,
                    "deduped": result.deduped,
                })
                total_signals += result.emitted
            except Exception as exc:
                cycles.append({"collector": "news_polling", "geo": geo, "error": str(exc)})

        # Phase 2: Run google_ads_transparency for each query+geo
        for geo in geos:
            for query in queries[:5]:
                try:
                    result = await workflow.execute_activity(
                        run_collector_activity,
                        CollectorParams(
                            collector="google_ads_transparency",
                            params={"search_terms": query, "region": geo, "max_pages": 2},
                        ),
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=_COLLECTOR_RETRY,
                    )
                    cycles.append({
                        "collector": "google_ads_transparency",
                        "geo": geo,
                        "query": query,
                        "emitted": result.emitted,
                        "deduped": result.deduped,
                    })
                    total_signals += result.emitted
                except Exception as exc:
                    cycles.append({
                        "collector": "google_ads_transparency",
                        "geo": geo,
                        "query": query,
                        "error": str(exc),
                    })

        # Phase 3: linkedin_jobs — hiring signals reveal growing companies
        _HIRING_KEYWORDS = [
            "performance marketing manager",
            "growth marketing head",
            "digital marketing D2C",
            "ecommerce marketing India",
            "head of marketing D2C brand",
        ]
        for geo in geos:
            for kw in _HIRING_KEYWORDS:
                try:
                    result = await workflow.execute_activity(
                        run_collector_activity,
                        CollectorParams(
                            collector="linkedin_jobs",
                            params={"keywords": kw, "location": "India", "max_pages": 2},
                        ),
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=_COLLECTOR_RETRY,
                    )
                    cycles.append({
                        "collector": "linkedin_jobs",
                        "geo": geo,
                        "keywords": kw,
                        "emitted": result.emitted,
                        "deduped": result.deduped,
                    })
                    total_signals += result.emitted
                except Exception as exc:
                    cycles.append({"collector": "linkedin_jobs", "geo": geo, "keywords": kw, "error": str(exc)})

        # Phase 4: meta_ad_library — brands actively spending on ads
        _META_QUERIES = [
            "Indian D2C brand funding 2024 2025",
            "India direct-to-consumer startup Series A",
            "new D2C brand launch India ecommerce",
            "Indian beauty brand online store",
            "India fashion D2C startup",
            "Indian health wellness D2C brand",
            "D2C brand India revenue crore",
            "Indian consumer brand digital marketing",
            "India ecommerce brand Series B funding",
            "new Indian lifestyle brand online",
        ]
        for geo in geos:
            for query in _META_QUERIES:
                try:
                    result = await workflow.execute_activity(
                        run_collector_activity,
                        CollectorParams(
                            collector="meta_ad_library",
                            params={"search_terms": query, "country": geo, "max_pages": 2},
                        ),
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=_COLLECTOR_RETRY,
                    )
                    cycles.append({
                        "collector": "meta_ad_library",
                        "geo": geo,
                        "query": query,
                        "emitted": result.emitted,
                        "deduped": result.deduped,
                    })
                    total_signals += result.emitted
                except Exception as exc:
                    cycles.append({
                        "collector": "meta_ad_library",
                        "geo": geo,
                        "query": query,
                        "error": str(exc),
                    })

        # Phase 5: seo_rank_tracking — find brands with SEO gaps
        for geo in geos:
            for query in queries[:5]:
                try:
                    result = await workflow.execute_activity(
                        run_collector_activity,
                        CollectorParams(
                            collector="seo_rank_tracking",
                            params={"queries": [query], "country": geo},
                        ),
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=_COLLECTOR_RETRY,
                    )
                    cycles.append({
                        "collector": "seo_rank_tracking",
                        "geo": geo,
                        "query": query,
                        "emitted": result.emitted,
                        "deduped": result.deduped,
                    })
                    total_signals += result.emitted
                except Exception as exc:
                    cycles.append({
                        "collector": "seo_rank_tracking",
                        "geo": geo,
                        "query": query,
                        "error": str(exc),
                    })

        # Phase 5b: youtube_social — social velocity signals
        _YT_QUERIES = [
            "Indian D2C beauty brand",
            "Indian fashion brand ecommerce",
            "D2C health wellness brand India",
            "Indian snack food brand online",
            "Indian electronics accessories brand",
        ]
        for search in _YT_QUERIES:
            try:
                result = await workflow.execute_activity(
                    run_collector_activity,
                    CollectorParams(
                        collector="youtube_social",
                        params={"search_terms": search, "max_channels": 10},
                    ),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_COLLECTOR_RETRY,
                )
                cycles.append({
                    "collector": "youtube_social",
                    "search": search,
                    "emitted": result.emitted,
                    "deduped": result.deduped,
                })
                total_signals += result.emitted
            except Exception as exc:
                cycles.append({"collector": "youtube_social", "search": search, "error": str(exc)})

        # Phase 5c: app_store — brands with mobile apps (Google Play)
        for query in queries[:3]:
            try:
                result = await workflow.execute_activity(
                    run_collector_activity,
                    CollectorParams(
                        collector="app_store",
                        params={"search_terms": query, "country": "in", "max_results": 10},
                    ),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_COLLECTOR_RETRY,
                )
                cycles.append({
                    "collector": "app_store",
                    "query": query,
                    "emitted": result.emitted,
                    "deduped": result.deduped,
                })
                total_signals += result.emitted
            except Exception as exc:
                cycles.append({"collector": "app_store", "query": query, "error": str(exc)})

        # Phase 5d: ios_app_store — brands with iOS apps
        _IOS_QUERIES = [
            "shopping india",
            "beauty skincare",
            "fashion online",
            "health wellness",
            "food delivery",
        ]
        for query in _IOS_QUERIES:
            try:
                result = await workflow.execute_activity(
                    run_collector_activity,
                    CollectorParams(
                        collector="ios_app_store",
                        params={"search_terms": query, "country": "in", "max_results": 10},
                    ),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_COLLECTOR_RETRY,
                )
                cycles.append({
                    "collector": "ios_app_store",
                    "query": query,
                    "emitted": result.emitted,
                    "deduped": result.deduped,
                })
                total_signals += result.emitted
            except Exception as exc:
                cycles.append({"collector": "ios_app_store", "query": query, "error": str(exc)})

        # Phase 5e: google_trends — rising brand interest signals
        _TRENDS_QUERIES = [
            "online shopping",
            "skincare",
            "fashion shopping",
            "health supplements",
            "home decor online",
        ]
        for query in _TRENDS_QUERIES:
            try:
                result = await workflow.execute_activity(
                    run_collector_activity,
                    CollectorParams(
                        collector="google_trends",
                        params={"search_terms": query, "geo": "IN"},
                    ),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_COLLECTOR_RETRY,
                )
                cycles.append({
                    "collector": "google_trends",
                    "query": query,
                    "emitted": result.emitted,
                    "deduped": result.deduped,
                })
                total_signals += result.emitted
            except Exception as exc:
                cycles.append({"collector": "google_trends", "query": query, "error": str(exc)})

        # Phase 5f: cert_transparency — earliest-possible brand discovery via SSL certs
        _CERT_SEARCHES = [
            "shop.in", "store.in", "buy.in",
            "myshopify.com",
        ]
        for search in _CERT_SEARCHES:
            try:
                result = await workflow.execute_activity(
                    run_collector_activity,
                    CollectorParams(
                        collector="cert_transparency",
                        params={"search_terms": search, "max_results": 50},
                    ),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_COLLECTOR_RETRY,
                )
                cycles.append({
                    "collector": "cert_transparency",
                    "search": search,
                    "emitted": result.emitted,
                    "deduped": result.deduped,
                })
                total_signals += result.emitted
            except Exception as exc:
                cycles.append({"collector": "cert_transparency", "search": search, "error": str(exc)})

        # Phase 5g: common_crawl — bulk Shopify/ecommerce store extraction
        try:
            result = await workflow.execute_activity(
                run_collector_activity,
                CollectorParams(
                    collector="common_crawl",
                    params={"max_results": 50},
                ),
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=_COLLECTOR_RETRY,
            )
            cycles.append({
                "collector": "common_crawl",
                "emitted": result.emitted,
                "deduped": result.deduped,
            })
            total_signals += result.emitted
        except Exception as exc:
            cycles.append({"collector": "common_crawl", "error": str(exc)})

        # Phase 5h: hn_discovery — early-stage brand mentions on Hacker News
        _HN_QUERIES = [
            "D2C brand India",
            "Indian ecommerce startup",
            "Show HN India shop",
        ]
        for search in _HN_QUERIES:
            try:
                result = await workflow.execute_activity(
                    run_collector_activity,
                    CollectorParams(
                        collector="hn_discovery",
                        params={"search_terms": search, "max_results": 20},
                    ),
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=_COLLECTOR_RETRY,
                )
                cycles.append({
                    "collector": "hn_discovery",
                    "search": search,
                    "emitted": result.emitted,
                    "deduped": result.deduped,
                })
                total_signals += result.emitted
            except Exception as exc:
                cycles.append({"collector": "hn_discovery", "search": search, "error": str(exc)})

        # Phase 5i: open_food_facts — Indian food/FMCG D2C brands
        _FOOD_QUERIES = [
            "organic snacks",
            "healthy chips",
            "protein bar",
            "herbal tea",
            "millet cookies",
        ]
        for search in _FOOD_QUERIES:
            try:
                result = await workflow.execute_activity(
                    run_collector_activity,
                    CollectorParams(
                        collector="open_food_facts",
                        params={"search_terms": search, "country": "india", "max_results": 30},
                    ),
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=_COLLECTOR_RETRY,
                )
                cycles.append({
                    "collector": "open_food_facts",
                    "search": search,
                    "emitted": result.emitted,
                    "deduped": result.deduped,
                })
                total_signals += result.emitted
            except Exception as exc:
                cycles.append({"collector": "open_food_facts", "search": search, "error": str(exc)})

        # Phase 5j: urlscan_search — pre-scanned ecommerce sites (no crawling needed)
        _URLSCAN_QUERIES = [
            "domain:shopify.in",
            "domain:myshopify.com",
            "page.server:Shopify",
        ]
        for uquery in _URLSCAN_QUERIES:
            try:
                result = await workflow.execute_activity(
                    run_collector_activity,
                    CollectorParams(
                        collector="urlscan_search",
                        params={"search_query": uquery, "max_results": 30},
                    ),
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=_COLLECTOR_RETRY,
                )
                cycles.append({
                    "collector": "urlscan_search",
                    "query": uquery,
                    "emitted": result.emitted,
                    "deduped": result.deduped,
                })
                total_signals += result.emitted
            except Exception as exc:
                cycles.append({"collector": "urlscan_search", "query": uquery, "error": str(exc)})

        # Phase 5k: Vane AI discovery — find brands no other collector catches
        _ICP_FILTER = (
            " ONLY include brands that: (a) have their own DTC online store, "
            "(b) <500 employees, (c) founded after 2015. "
            "Exclude marketplace-only, B2B, conglomerate subsidiaries."
        )
        _VANE_DISCOVERY_QUERIES = [
            "List 20 Indian D2C ecommerce brands that raised Series A or B in 2024-2025. "
            "Include: brand name, website URL, funding amount, category." + _ICP_FILTER,
            "Top 15 emerging Indian D2C beauty and skincare brands with own website. "
            "Include brand name, website, revenue if known." + _ICP_FILTER,
            "New Indian D2C fashion and lifestyle brands launched 2023-2025 with own store. "
            "List brand name, website URL, city of HQ." + _ICP_FILTER,
            "Indian D2C health, wellness, nutrition brands selling via own website. "
            "List brand name, website, product category." + _ICP_FILTER,
            "Indian D2C home decor, furniture brands with ecommerce stores. "
            "Include brand name, website URL, employee count." + _ICP_FILTER,
            "Indian D2C food, snack, beverage brands with own online store, founded after 2018. "
            "List brand name, website, funding, category." + _ICP_FILTER,
            "Indian D2C pet care and baby care brands with own ecommerce website. "
            "List brand name, website, revenue." + _ICP_FILTER,
        ]
        try:
            result = await workflow.execute_activity(
                run_collector_activity,
                CollectorParams(
                    collector="vane_discovery",
                    params={"queries": _VANE_DISCOVERY_QUERIES},
                ),
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=_COLLECTOR_RETRY,
            )
            cycles.append({
                "collector": "vane_discovery",
                "emitted": result.emitted,
                "deduped": result.deduped,
            })
            total_signals += result.emitted
        except Exception as exc:
            cycles.append({"collector": "vane_discovery", "error": str(exc)})

        # Phase 5l: enrich recently discovered domains with tech_stack, pagespeed,
        #           rdap_whois, shodan_internetdb, ipinfo_geo
        try:
            domains_result: RecentDomainsResult = await workflow.execute_activity(
                fetch_recent_brand_domains_activity,
                RecentDomainsRequest(
                    since_minutes=inp.fan_out_since_minutes,
                    max_domains=20,
                ),
                start_to_close_timeout=timedelta(seconds=30),
            )
            recent_domains = domains_result.domains
        except Exception as exc:
            recent_domains = []
            cycles.append({"phase": "fetch_domains", "error": str(exc)})

        if recent_domains:
            _ENRICHMENT_COLLECTORS = [
                "tech_stack", "pagespeed",
                "rdap_whois", "shodan_internetdb", "ipinfo_geo",
                "affiliate_program", "social_presence", "content_blog",
                "ads_txt", "email_maturity",
                "crunchbase_funding", "tracxn_funding",
                "tofler_company", "vane_research",
            ]
            for collector_name in _ENRICHMENT_COLLECTORS:
                try:
                    result = await workflow.execute_activity(
                        run_collector_activity,
                        CollectorParams(
                            collector=collector_name,
                            params={"domains": recent_domains},
                        ),
                        start_to_close_timeout=timedelta(minutes=20),
                        retry_policy=_COLLECTOR_RETRY,
                    )
                    cycles.append({
                        "collector": collector_name,
                        "domains_checked": len(recent_domains),
                        "emitted": result.emitted,
                        "deduped": result.deduped,
                    })
                    total_signals += result.emitted
                except Exception as exc:
                    cycles.append({"collector": collector_name, "error": str(exc)})

        # Phase 6: Enrich domain-less signal hints via SearXNG
        if total_signals > 0:
            try:
                enrich: EnrichHintsResult = await workflow.execute_activity(
                    enrich_unresolved_hints_activity,
                    EnrichHintsRequest(
                        since_minutes=inp.fan_out_since_minutes,
                        max_hints=20,
                    ),
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=_COLLECTOR_RETRY,
                )
                cycles.append({
                    "phase": "enrich_hints",
                    "resolved": enrich.resolved,
                    "failed": enrich.failed,
                })
            except Exception as exc:
                cycles.append({"phase": "enrich_hints", "error": str(exc)})

        # Phase 7: Fan out DossierWF for newly discovered brands (with pre-qualify gate)
        fanned: list[int] = []
        if total_signals > 0:
            try:
                fan: FanOutResult = await workflow.execute_activity(
                    fan_out_new_brands_activity,
                    inp.fan_out_since_minutes,
                    start_to_close_timeout=timedelta(seconds=30),
                )
                candidates = fan.brand_ids[: inp.max_fan_out]

                # Pre-qualify: only fan out brands with enough signal evidence
                pq: PreQualifyResult = await workflow.execute_activity(
                    pre_qualify_brands_activity,
                    PreQualifyRequest(brand_ids=candidates),
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                targets = pq.qualified
                cycles.append({
                    "phase": "pre_qualify",
                    "candidates": len(candidates),
                    "qualified": len(targets),
                    "rejected": len(pq.rejected),
                })

                for chunk in _chunks(targets, 3):
                    child_handles = [
                        workflow.start_child_workflow(
                            DossierWF.run,
                            DossierWFInput(brand_id=bid, reason="auto_discovery"),
                            id=f"auto-dossier-{bid}-{workflow.info().run_id[:12]}",
                            retry_policy=RetryPolicy(maximum_attempts=2),
                            parent_close_policy=ParentClosePolicy.ABANDON,
                        )
                        for bid in chunk
                    ]
                    handles = [await h for h in child_handles]
                    for bid, _h in zip(chunk, handles, strict=True):
                        fanned.append(bid)
            except Exception as exc:
                cycles.append({"phase": "fan_out", "error": str(exc)})

        return AutoDiscoveryWFResult(
            cycles=cycles,
            total_signals=total_signals,
            fanned_out_brand_ids=fanned,
        )


def _chunks(seq: list[int], n: int) -> list[list[int]]:
    return [seq[i : i + n] for i in range(0, len(seq), n)]
