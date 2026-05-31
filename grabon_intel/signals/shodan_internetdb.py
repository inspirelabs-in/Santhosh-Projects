"""Shodan InternetDB enrichment collector.

Queries Shodan's free InternetDB API for IP-level metadata: open
ports, tags, hostnames, and CPEs. Hosting infrastructure and CDN
detection serve as brand maturity signals.

Free — no API key, no signup. Public endpoint.
"""
from __future__ import annotations

import datetime as dt
import socket
from collections.abc import AsyncIterator

import httpx

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_CDN_INDICATORS = {
    "cloudflare": "Cloudflare",
    "akamai": "Akamai",
    "fastly": "Fastly",
    "cloudfront": "CloudFront",
    "stackpath": "StackPath",
    "sucuri": "Sucuri",
    "incapsula": "Imperva",
    "maxcdn": "MaxCDN",
}

_CLOUD_INDICATORS = {
    "amazon": "AWS",
    "aws": "AWS",
    "google": "GCP",
    "microsoft": "Azure",
    "digitalocean": "DigitalOcean",
    "linode": "Linode",
    "vultr": "Vultr",
    "hetzner": "Hetzner",
}


class ShodanInternetDBCollector(Collector):
    """Enrich domains with IP-level infrastructure data.

    Parameters:
        domains: list[str] of domains to look up. REQUIRED.
    """

    name = "shodan_internetdb"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("shodan_internetdb.no_domains")
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        async with httpx.AsyncClient(timeout=15.0) as client:
            for domain in domains:
                domain = domain.strip().lower().removeprefix("www.")
                if not domain:
                    continue

                try:
                    ip = await _resolve_domain(domain)
                    if not ip:
                        continue

                    info = await _fetch_internetdb(client, ip)
                    if not info:
                        continue

                    ports = info.get("ports", [])
                    tags = info.get("tags", [])
                    hostnames = info.get("hostnames", [])
                    cpes = info.get("cpes", [])

                    cdn = _detect_cdn(hostnames, cpes, tags)
                    cloud = _detect_cloud(hostnames, cpes)
                    has_https = 443 in ports
                    has_http = 80 in ports

                    maturity = _score_maturity(ports, tags, cdn, has_https)

                    yield SignalEvent(
                        type="infra.hosting",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=float(maturity),
                        value_text=f"infra_maturity={maturity} cdn={cdn} cloud={cloud}",
                        payload={
                            "domain": domain,
                            "ip": ip,
                            "ports": ports,
                            "tags": tags,
                            "hostnames": hostnames[:10],
                            "cdn": cdn,
                            "cloud": cloud,
                            "has_https": has_https,
                            "has_http": has_http,
                            "num_cpes": len(cpes),
                            "maturity_score": maturity,
                        },
                        dedupe_key=f"sdb:{domain}:{day}",
                    )
                except Exception as exc:
                    log.debug("shodan_internetdb.failed", domain=domain, error=str(exc))


async def _resolve_domain(domain: str) -> str | None:
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        results = await loop.getaddrinfo(domain, None, family=socket.AF_INET)
        if results:
            return results[0][4][0]
    except (socket.gaierror, OSError):
        pass
    return None


async def _fetch_internetdb(
    client: httpx.AsyncClient, ip: str
) -> dict | None:
    resp = await client.get(f"https://internetdb.shodan.io/{ip}")
    if resp.status_code != 200:
        return None
    return resp.json()


def _detect_cdn(hostnames: list, cpes: list, tags: list) -> str:
    all_text = " ".join(hostnames + cpes + tags).lower()
    for key, name in _CDN_INDICATORS.items():
        if key in all_text:
            return name
    return "none"


def _detect_cloud(hostnames: list, cpes: list) -> str:
    all_text = " ".join(hostnames + cpes).lower()
    for key, name in _CLOUD_INDICATORS.items():
        if key in all_text:
            return name
    return "unknown"


def _score_maturity(
    ports: list[int], tags: list[str], cdn: str, has_https: bool
) -> int:
    score = 0
    if has_https:
        score += 30
    if cdn != "none":
        score += 30
    if len(ports) <= 3:
        score += 20
    if "ecommerce" in tags or "shop" in tags:
        score += 20
    return min(score, 100)
