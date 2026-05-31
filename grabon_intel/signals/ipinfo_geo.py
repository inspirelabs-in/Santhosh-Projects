"""IPinfo Lite geolocation enrichment.

Resolves brand domains to IPs and checks hosting geo, ASN, and
organization. Confirms India-hosted brands vs offshore hosting.
ASN clustering reveals infrastructure patterns.

Free tier — unlimited requests with free token (signup at ipinfo.io).
Works without token at lower rate limits.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import socket
from collections.abc import AsyncIterator

import httpx

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)


class IPInfoGeoCollector(Collector):
    """Enrich domains with IP geolocation and ASN data.

    Parameters:
        domains: list[str] of domains to look up. REQUIRED.
        api_token: IPinfo API token (free signup). Optional.
    """

    name = "ipinfo_geo"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("ipinfo_geo.no_domains")
            return

        token = self.params.get("api_token") or ""

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

                    info = await _fetch_ipinfo(client, ip, token)
                    if not info:
                        continue

                    country = info.get("country", "").upper()
                    org = info.get("org", "")
                    city = info.get("city", "")
                    region = info.get("region", "")
                    is_india = country == "IN"

                    asn = ""
                    asn_name = ""
                    if org:
                        parts = org.split(" ", 1)
                        asn = parts[0] if parts[0].startswith("AS") else ""
                        asn_name = parts[1] if len(parts) > 1 else org

                    yield SignalEvent(
                        type="infra.geo",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=1.0 if is_india else 0.0,
                        value_text=f"hosted_in={country} org={asn_name[:40]}",
                        payload={
                            "domain": domain,
                            "ip": ip,
                            "country": country,
                            "city": city,
                            "region": region,
                            "org": org,
                            "asn": asn,
                            "asn_name": asn_name,
                            "is_india_hosted": is_india,
                        },
                        dedupe_key=f"ipgeo:{domain}:{day}",
                    )
                except Exception as exc:
                    log.debug("ipinfo_geo.failed", domain=domain, error=str(exc))


async def _resolve_domain(domain: str) -> str | None:
    try:
        loop = asyncio.get_event_loop()
        results = await loop.getaddrinfo(domain, None, family=socket.AF_INET)
        if results:
            return results[0][4][0]
    except (socket.gaierror, OSError):
        pass
    return None


async def _fetch_ipinfo(
    client: httpx.AsyncClient, ip: str, token: str
) -> dict | None:
    url = f"https://ipinfo.io/{ip}/json"
    params = {}
    if token:
        params["token"] = token

    resp = await client.get(url, params=params)
    if resp.status_code != 200:
        return None
    return resp.json()
