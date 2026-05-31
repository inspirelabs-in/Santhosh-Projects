"""Vane AI search collector — PRIMARY research engine via self-hosted Perplexity clone.

Uses Vane (Perplexica) to run AI-powered web searches that synthesize data
from multiple sources into structured answers. This is the main source for
company intelligence: revenue, employee count, funding rounds, founders,
headquarters, founding year, competitive landscape, digital presence.

Requires: Vane service running (docker-compose), connected to SearXNG.
LLM provider auto-detected from OPENAI_API_KEY env var.
"""
from __future__ import annotations

import datetime as dt
import json as _json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import get_settings
from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_COMMON_PREFIXES = ("the", "my", "get", "go", "try", "use", "hey", "its", "buy")

_SYSTEM_PROMPT = (
    "You are a business intelligence analyst. STRICT RULES:\n"
    "1. ONLY state facts you find in search results. Never guess or estimate.\n"
    "2. For every claim, cite the source in [brackets] at end of sentence.\n"
    "3. If a data point is not found in any source, explicitly say 'Not found'.\n"
    "4. Use exact numbers from sources — never round or approximate.\n"
    "5. Distinguish between confirmed facts and unverified claims.\n"
    "6. Prefer official sources: company website, Crunchbase, Tracxn, LinkedIn, "
    "MCA filings, news articles from Economic Times, Inc42, YourStory, Entrackr."
)


def _strip_common_prefixes(stem: str) -> str:
    lower = stem.lower()
    for prefix in _COMMON_PREFIXES:
        if lower.startswith(prefix) and len(lower) > len(prefix) + 2:
            return lower[len(prefix):]
    return lower


class VaneResearchCollector(Collector):
    """AI-synthesized brand research via Vane (self-hosted Perplexity).

    Parameters:
        domains: list[str] of domains to research. REQUIRED.
        brand_names: list[str] optional brand names for better queries.
    """

    name = "vane_research"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("vane_research.no_domains")
            return

        vane_url = get_settings().vane_url
        if not vane_url:
            log.warning("vane_research.no_vane_url")
            return

        brand_names = self.params.get("brand_names") or []
        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        provider_config = await _get_provider_config(vane_url)
        if not provider_config:
            log.warning("vane_research.no_providers")
            return

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            for idx, domain in enumerate(domains):
                domain = domain.strip().lower().removeprefix("www.")
                if not domain:
                    continue

                brand_stem = domain.split(".")[0]
                clean_stem = _strip_common_prefixes(brand_stem)
                brand_name = brand_names[idx] if idx < len(brand_names) else clean_stem

                # Query 1: Company profile + funding (combined — most critical data)
                company_data = await _vane_search(
                    client, vane_url, provider_config,
                    f"{brand_name} ({domain}) company profile and funding: "
                    f"1. Headquarters city 2. Founded year 3. Founders (full names) "
                    f"4. Employee count 5. Funding rounds (amount, date, investors) "
                    f"6. Total funding raised 7. Revenue or ARR "
                    f"8. Legal entity name. "
                    f"Search Crunchbase, Tracxn, LinkedIn, Inc42, YourStory.",
                )

                if company_data:
                    yield SignalEvent(
                        type="ai_research.company",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=None,
                        value_text=company_data["message"][:500],
                        payload={
                            "query_type": "company_overview",
                            "full_answer": company_data["message"],
                            "sources": company_data.get("sources", []),
                            "source_count": len(company_data.get("sources", [])),
                        },
                        dedupe_key=f"vane-company:{brand_stem}:{day}",
                    )

                # Query 2: Recent activity — news, partnerships, product launches, hiring
                activity_data = await _vane_search(
                    client, vane_url, provider_config,
                    f"{brand_name} ({domain}) recent news 2024 2025: "
                    f"1. New product launches or category expansion "
                    f"2. Strategic partnerships or collaborations "
                    f"3. Key hires (CMO, VP Marketing, Head of Growth) "
                    f"4. New market entry or international expansion "
                    f"5. Awards, press coverage, or media mentions. "
                    f"Only include news from the last 12 months.",
                )

                if activity_data:
                    yield SignalEvent(
                        type="ai_research.activity",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=None,
                        value_text=activity_data["message"][:500],
                        payload={
                            "query_type": "recent_activity",
                            "full_answer": activity_data["message"],
                            "sources": activity_data.get("sources", []),
                            "source_count": len(activity_data.get("sources", [])),
                        },
                        dedupe_key=f"vane-activity:{brand_stem}:{day}",
                    )

                # Query 3: Digital presence verification (social, ads, blog)
                digital_data = await _vane_search(
                    client, vane_url, provider_config,
                    f"What social media accounts does {brand_name} ({domain}) operate? "
                    f"List their Instagram, YouTube, Facebook, Twitter/X, LinkedIn handles "
                    f"with follower counts. Does {brand_name} run paid ads on Meta or Google? "
                    f"Do they have an active blog? What email marketing tools do they use? "
                    f"Cite the source URL for each finding.",
                )

                if digital_data:
                    yield SignalEvent(
                        type="ai_research.digital",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=None,
                        value_text=digital_data["message"][:500],
                        payload={
                            "query_type": "digital_presence",
                            "full_answer": digital_data["message"],
                            "sources": digital_data.get("sources", []),
                            "source_count": len(digital_data.get("sources", [])),
                        },
                        dedupe_key=f"vane-digital:{brand_stem}:{day}",
                    )

                # Query 4: Competitive landscape
                competitive_data = await _vane_search(
                    client, vane_url, provider_config,
                    f"Who are {brand_name}'s ({domain}) top 5 direct competitors in India? "
                    f"For each competitor list: company name, website, funding raised, "
                    f"employee count. What product category does {brand_name} operate in?",
                )

                if competitive_data:
                    yield SignalEvent(
                        type="ai_research.competitive",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=None,
                        value_text=competitive_data["message"][:500],
                        payload={
                            "query_type": "competitive_landscape",
                            "full_answer": competitive_data["message"],
                            "sources": competitive_data.get("sources", []),
                            "source_count": len(competitive_data.get("sources", [])),
                        },
                        dedupe_key=f"vane-competitive:{brand_stem}:{day}",
                    )


async def _get_provider_config(vane_url: str) -> dict[str, Any] | None:
    """Fetch available LLM providers from Vane and pick the best one."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{vane_url.rstrip('/')}/api/providers")
            if resp.status_code != 200:
                log.warning("vane_research.providers_error", status=resp.status_code)
                return None

            data = resp.json()
            providers = data.get("providers", [])
            if not providers:
                log.warning("vane_research.no_providers_configured")
                return None

            chat_provider = None
            embed_provider = None
            for provider in providers:
                if not chat_provider and provider.get("chatModels"):
                    _PREFERRED = ("gpt-4o-mini", "gpt-4.1-nano", "gpt-4.1-mini")
                    models = provider["chatModels"]
                    pick = next((m for m in models if m["key"] in _PREFERRED), models[0])
                    chat_provider = {"providerId": provider["id"], "key": pick["key"]}
                if not embed_provider and provider.get("embeddingModels"):
                    embed_provider = {
                        "providerId": provider["id"],
                        "key": provider["embeddingModels"][0]["key"],
                    }
                if chat_provider and embed_provider:
                    break

            if not chat_provider or not embed_provider:
                return None
            return {"chatModel": chat_provider, "embeddingModel": embed_provider}

    except Exception as exc:
        log.warning("vane_research.providers_failed", error=str(exc))
        return None


async def _vane_search(
    client: httpx.AsyncClient,
    vane_url: str,
    provider_config: dict[str, Any],
    query: str,
) -> dict[str, Any] | None:
    """Execute a single Vane AI search query."""
    try:
        payload = {
            **provider_config,
            "sources": ["web"],
            "optimizationMode": "balanced",
            "query": query,
            "stream": False,
            "systemInstructions": _SYSTEM_PROMPT,
        }

        resp = await client.post(
            f"{vane_url.rstrip('/')}/api/search",
            json=payload,
        )

        if resp.status_code != 200:
            log.debug("vane_research.search_error", status=resp.status_code, query=query[:60])
            return None

        data = resp.json()
        message = data.get("message", "")
        sources = data.get("sources", [])

        if not message or len(message) < 20:
            return None

        _NO_DATA = ("could not find", "no relevant information", "not found",
                     "unable to find", "no results", "search again")
        lower_msg = message.lower()
        if any(phrase in lower_msg for phrase in _NO_DATA) and len(message) < 200:
            return None

        clean_sources = []
        for src in sources[:10]:
            meta = src.get("metadata", {})
            clean_sources.append({
                "title": meta.get("title", ""),
                "url": meta.get("url", ""),
            })

        return {
            "message": message,
            "sources": clean_sources,
        }

    except Exception as exc:
        log.debug("vane_research.search_failed", error=str(exc), query=query[:60])
        return None
