"""Certificate Transparency log discovery via crt.sh.

Queries crt.sh for recently issued SSL certificates matching brand
keywords. New D2C brands get SSL certs before they appear in ads or
news — this is often the earliest discovery signal possible.

Free — no API key, no signup. Public CT log aggregator.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator

import httpx

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_NOISE_DOMAINS = re.compile(
    r"(cloudflare|akamai|fastly|amazonaws|github|google|"
    r"microsoft|azure|digitalocean|heroku|netlify|vercel|"
    r"letsencrypt|sectigo|godaddy|namecheap|cpanel|plesk|"
    r"sentry\.|bugsnag|datadog|newrelic|localhost|"
    r"\.gov\.|\.edu\.|\.mil\.)",
    re.I,
)

_INTERESTING_PATTERNS = re.compile(
    r"\.(in|co\.in|com|shop|store|online|io)$", re.I
)


class CertTransparencyCollector(Collector):
    """Discover new domains via Certificate Transparency logs.

    Parameters:
        search_terms: keyword to search in cert CN/SAN (e.g. "shop").
        wildcard: if True, wraps search_terms with % wildcards (default True).
        max_results: max certs to process (default 100).
        exclude_expired: skip expired certs (default True).
    """

    name = "cert_transparency"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        search = (self.params.get("search_terms") or "").strip()
        if not search:
            log.warning("cert_transparency.no_search_terms")
            return

        wildcard = self.params.get("wildcard", True)
        max_results = self.params.get("max_results", 100)
        exclude_expired = self.params.get("exclude_expired", True)

        query = f"%.{search}.%" if wildcard else search

        try:
            entries = await _fetch_certs(query, max_results, exclude_expired)
        except Exception as exc:
            log.warning("cert_transparency.fetch_failed", error=str(exc))
            return

        if not entries:
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()
        seen_domains: set[str] = set()

        for entry in entries:
            domain = _extract_domain(entry.get("common_name", ""))
            if not domain or domain in seen_domains:
                continue
            if _NOISE_DOMAINS.search(domain):
                continue
            if not _INTERESTING_PATTERNS.search(domain):
                continue

            seen_domains.add(domain)
            brand_name = _domain_to_brand(domain)

            not_before = entry.get("not_before", "")
            not_after = entry.get("not_after", "")
            issuer = entry.get("issuer_name", "")

            yield SignalEvent(
                type="cert.new_domain",
                source=self.name,
                observed_at=now,
                brand_domain=domain,
                brand_name=brand_name,
                value_text=f"SSL cert issued: {domain}",
                payload={
                    "domain": domain,
                    "issuer": issuer,
                    "not_before": not_before,
                    "not_after": not_after,
                    "search_term": search,
                },
                dedupe_key=f"crt:{domain}:{day}",
            )


async def _fetch_certs(
    query: str, max_results: int, exclude_expired: bool
) -> list[dict]:
    params: dict[str, str] = {
        "q": query,
        "output": "json",
        "deduplicate": "Y",
    }
    if exclude_expired:
        params["exclude"] = "expired"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get("https://crt.sh/", params=params)
        if resp.status_code != 200:
            log.debug("cert_transparency.http_error", status=resp.status_code)
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        return data[:max_results]


def _extract_domain(cn: str) -> str | None:
    cn = cn.strip().lower()
    if cn.startswith("*."):
        cn = cn[2:]
    cn = cn.removeprefix("www.")
    if not cn or "." not in cn:
        return None
    if " " in cn:
        return None
    return cn


def _domain_to_brand(domain: str) -> str:
    name = domain.split(".")[0]
    name = re.sub(r"[-_]", " ", name)
    return name.strip().title()
