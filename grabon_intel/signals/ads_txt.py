"""ads.txt collector — detects programmatic advertising usage.

Fetches /ads.txt from target domains to identify brands running
programmatic display/video ads. The presence and content of ads.txt
reveals SSP/exchange relationships and programmatic maturity.

Free — just HTTP fetches. No API key needed.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_KNOWN_SSPS = {
    "google.com": "Google AdX",
    "googleads.g.doubleclick.net": "Google AdX",
    "openx.com": "OpenX",
    "appnexus.com": "AppNexus/Xandr",
    "rubiconproject.com": "Rubicon/Magnite",
    "pubmatic.com": "PubMatic",
    "indexexchange.com": "Index Exchange",
    "sovrn.com": "Sovrn",
    "33across.com": "33Across",
    "criteo.com": "Criteo",
    "amazon-adsystem.com": "Amazon Publisher Services",
    "media.net": "Media.net",
    "smartadserver.com": "Smart",
    "triplelift.com": "TripleLift",
    "spotxchange.com": "SpotX",
    "adcolony.com": "AdColony",
    "inmobi.com": "InMobi",
    "verizonmedia.com": "Verizon Media",
}


class AdsTxtCollector(Collector):
    """Check ads.txt for a list of domains.

    Parameters:
        domains: list[str] of domains to check. REQUIRED.
    """

    name = "ads_txt"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("ads_txt.no_domains")
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": "GrabonIntelBot/0.2"},
        ) as client:
            for domain in domains:
                domain = domain.strip().lower().removeprefix("www.")
                if not domain:
                    continue

                try:
                    resp = await client.get(f"https://{domain}/ads.txt")
                    if resp.status_code != 200:
                        yield SignalEvent(
                            type="programmatic.ads_txt",
                            source=self.name,
                            observed_at=now,
                            brand_domain=domain,
                            value_num=0.0,
                            value_text="no_ads_txt",
                            payload={
                                "domain": domain,
                                "has_ads_txt": False,
                                "status_code": resp.status_code,
                            },
                            dedupe_key=f"adstxt:{domain}:{day}",
                        )
                        continue

                    parsed = _parse_ads_txt(resp.text)
                    ssps = parsed["ssps"]
                    direct_count = parsed["direct"]
                    reseller_count = parsed["reseller"]
                    total = direct_count + reseller_count

                    yield SignalEvent(
                        type="programmatic.ads_txt",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=float(total),
                        value_text=f"{direct_count} direct, {reseller_count} reseller",
                        payload={
                            "domain": domain,
                            "has_ads_txt": True,
                            "total_entries": total,
                            "direct_count": direct_count,
                            "reseller_count": reseller_count,
                            "ssps": sorted(ssps),
                            "maturity": _classify_maturity(total, direct_count),
                        },
                        dedupe_key=f"adstxt:{domain}:{day}",
                    )

                except httpx.HTTPError as exc:
                    log.debug("ads_txt.fetch_failed", domain=domain, error=str(exc))


def _parse_ads_txt(content: str) -> dict[str, Any]:
    ssps: set[str] = set()
    direct = 0
    reseller = 0

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        exchange_domain = parts[0].lower()
        relationship = parts[2].lower()

        if relationship == "direct":
            direct += 1
        elif relationship == "reseller":
            reseller += 1

        ssp_name = _KNOWN_SSPS.get(exchange_domain, exchange_domain)
        ssps.add(ssp_name)

    return {"ssps": ssps, "direct": direct, "reseller": reseller}


def _classify_maturity(total: int, direct: int) -> str:
    if total == 0:
        return "none"
    if total <= 5 and direct <= 2:
        return "basic"
    if total <= 20:
        return "moderate"
    if total <= 50:
        return "advanced"
    return "enterprise"
