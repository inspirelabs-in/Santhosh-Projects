"""Vane AI discovery collector — finds NEW brands via AI-powered search.

Unlike vane_research (which enriches known domains), this collector asks
Vane open-ended discovery questions like "top Indian D2C beauty brands 2024"
and extracts brand names + domains from the AI-synthesized answers.

Requires: Vane service running (docker-compose).
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import get_settings
from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_DOMAIN_RE = re.compile(
    r"(?:https?://)?(?:www\.)?([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z]{2,})+)",
    re.I,
)

_BRAND_DOMAIN_RE = re.compile(
    r"\*\*([A-Za-z0-9][A-Za-z0-9 &'-]{1,40})\*\*"
    r"[^()\n]{0,80}?"
    r"(?:"
    r"\(\s*(?:https?://)?(?:www\.)?([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z]{2,})+)\s*\)"
    r"|"
    r"(?:https?://)?(?:www\.)?([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z]{2,})+)(?=[\s,;)\]]|$)"
    r")",
    re.I,
)

_ICP_CONSTRAINT = (
    " ONLY include brands that: (a) have their own DTC online store (not just marketplace), "
    "(b) have fewer than 500 employees, (c) were founded after 2015. "
    "Exclude marketplace-only brands, B2B companies, and subsidiaries of large conglomerates."
)

_DEFAULT_QUERIES = [
    "List 20 Indian D2C ecommerce brands that raised Series A or B funding in 2024 or 2025. "
    "For each include: brand name, website URL, funding amount, category." + _ICP_CONSTRAINT,

    "Top 15 emerging Indian direct-to-consumer beauty and skincare brands with own website. "
    "Include brand name, website, and approximate revenue if known." + _ICP_CONSTRAINT,

    "New Indian D2C fashion and lifestyle brands launched in 2023-2025 with own online store. "
    "List brand name, website URL, and city of headquarters." + _ICP_CONSTRAINT,

    "Indian D2C health, wellness, and nutrition brands selling through their own website. "
    "List brand name, website, and product category." + _ICP_CONSTRAINT,

    "Fast-growing Indian D2C home decor, furniture, and lifestyle brands with ecommerce stores. "
    "Include brand name, website URL, and estimated employee count." + _ICP_CONSTRAINT,

    "Indian D2C food and snack brands with their own online store, founded after 2018. "
    "List brand name, website URL, funding raised, and product category." + _ICP_CONSTRAINT,

    "Indian D2C pet care and baby care brands with own ecommerce website. "
    "List brand name, website URL, and approximate revenue." + _ICP_CONSTRAINT,
]


class VaneDiscoveryCollector(Collector):
    """AI-powered brand discovery via Vane.

    Parameters:
        queries: list[str] optional custom discovery queries.
            Defaults to _DEFAULT_QUERIES if not provided.
    """

    name = "vane_discovery"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        queries = self.params.get("queries") or _DEFAULT_QUERIES
        vane_url = get_settings().vane_url
        if not vane_url:
            log.warning("vane_discovery.no_vane_url")
            return

        provider_config = await _get_provider_config(vane_url)
        if not provider_config:
            log.warning("vane_discovery.no_providers")
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            for query in queries:
                data = await _vane_search(client, vane_url, provider_config, query)
                if not data:
                    continue

                message = data["message"]
                brands_found = _extract_brands(message)

                for brand_name, domain in brands_found:
                    yield SignalEvent(
                        type="discovery.vane_ai",
                        source=self.name,
                        observed_at=now,
                        brand_name=brand_name,
                        brand_domain=domain,
                        value_num=None,
                        value_text=f"AI-discovered: {brand_name}",
                        payload={
                            "discovery_query": query[:200],
                            "source_count": len(data.get("sources", [])),
                        },
                        dedupe_key=f"vane-disc:{domain or brand_name}:{day}",
                    )


_NUMBERED_BRAND_RE = re.compile(
    r"(?:^|\n)\s*\d+[\.\)]\s*\*{0,2}"
    r"([A-Za-z][A-Za-z0-9 &'.-]{2,40}?)"
    r"\*{0,2}\s*[-–—:]+\s*[^()\n]{0,80}?"
    r"\(\s*(?:https?://)?(?:www\.)?([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z]{2,})+)\s*\)",
    re.I,
)


def _fuzzy_match_domain(name: str, domains: set[str]) -> str | None:
    """2-gram fuzzy match: if >=60% of name bigrams appear in a domain, match it."""
    slug = name.lower().replace(" ", "").replace("'", "").replace("-", "")
    if len(slug) < 3:
        return None
    name_bigrams = {slug[i:i+2] for i in range(len(slug) - 1)}
    best_domain = None
    best_score = 0.0
    for d in domains:
        d_clean = d.replace(".", "").replace("-", "")
        d_bigrams = {d_clean[i:i+2] for i in range(len(d_clean) - 1)}
        if not d_bigrams:
            continue
        overlap = len(name_bigrams & d_bigrams) / len(name_bigrams)
        if overlap > best_score and overlap >= 0.6:
            best_score = overlap
            best_domain = d
    return best_domain


def _extract_brands(text: str) -> list[tuple[str, str | None]]:
    """Extract (brand_name, domain) pairs from Vane AI answer text."""
    results: list[tuple[str, str | None]] = []
    seen_domains: set[str] = set()
    seen_names: set[str] = set()

    # Pass 1: **Brand** ... (domain.com) or **Brand** ... domain.com pattern
    for match in _BRAND_DOMAIN_RE.finditer(text):
        name = match.group(1).strip()
        domain = (match.group(2) or match.group(3) or "").strip().lower()
        if not domain:
            continue
        if domain not in seen_domains and name.lower() not in seen_names:
            seen_domains.add(domain)
            seen_names.add(name.lower())
            results.append((name, domain))

    # Pass 2: Numbered list pattern "1. Brand Name ... (domain.com)"
    for match in _NUMBERED_BRAND_RE.finditer(text):
        name = match.group(1).strip()
        domain = match.group(2).strip().lower()
        if domain not in seen_domains and name.lower() not in seen_names:
            seen_domains.add(domain)
            seen_names.add(name.lower())
            results.append((name, domain))

    # Pass 3: fuzzy matching for bold names without paired domains
    if len(results) < 5:
        bold_names = re.findall(r"\*\*([A-Za-z0-9][A-Za-z0-9 &'-]{1,40})\*\*", text)
        domains_in_text = _DOMAIN_RE.findall(text)
        domain_set = {d.lower() for d in domains_in_text} - seen_domains

        for name in bold_names:
            name = name.strip()
            if name.lower() in seen_names:
                continue
            # Exact slug match first
            name_slug = name.lower().replace(" ", "").replace("'", "")
            matched_domain = None
            for d in domain_set:
                if name_slug in d.replace(".", "").replace("-", ""):
                    matched_domain = d
                    break
            # Fuzzy 2-gram match fallback
            if not matched_domain:
                matched_domain = _fuzzy_match_domain(name, domain_set)
            if matched_domain and matched_domain not in seen_domains:
                seen_domains.add(matched_domain)
                seen_names.add(name.lower())
                results.append((name, matched_domain))
            elif not matched_domain:
                seen_names.add(name.lower())
                results.append((name, None))

    return results


async def _get_provider_config(vane_url: str) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{vane_url.rstrip('/')}/api/providers")
            if resp.status_code != 200:
                return None
            data = resp.json()
            providers = data.get("providers", [])
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
        log.warning("vane_discovery.providers_failed", error=str(exc))
        return None


async def _vane_search(
    client: httpx.AsyncClient,
    vane_url: str,
    provider_config: dict[str, Any],
    query: str,
) -> dict[str, Any] | None:
    try:
        payload = {
            **provider_config,
            "sources": ["web"],
            "optimizationMode": "balanced",
            "query": query,
            "stream": False,
            "systemInstructions": (
                "You are a business intelligence researcher. "
                "List REAL companies with their ACTUAL website URLs. "
                "For each company include the brand name and website domain. "
                "Only include companies you can verify from search results. "
                "Format each entry with **Brand Name** and (website.com)."
            ),
        }
        resp = await client.post(
            f"{vane_url.rstrip('/')}/api/search",
            json=payload,
        )
        if resp.status_code != 200:
            log.debug("vane_discovery.search_error", status=resp.status_code)
            return None

        data = resp.json()
        message = data.get("message", "")
        if not message or len(message) < 50:
            return None

        sources = data.get("sources", [])
        clean_sources = []
        for src in sources[:10]:
            meta = src.get("metadata", {})
            clean_sources.append({
                "title": meta.get("title", ""),
                "url": meta.get("url", ""),
            })
        return {"message": message, "sources": clean_sources}

    except Exception as exc:
        log.debug("vane_discovery.search_failed", error=str(exc))
        return None
