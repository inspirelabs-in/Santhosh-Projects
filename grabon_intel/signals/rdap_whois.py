"""RDAP (WHOIS replacement) domain enrichment.

Queries the IANA RDAP service for structured domain registration
data: creation date, expiry, registrar, nameservers. Domain age
and nameserver fingerprint are strong brand maturity signals.

Free — no API key, no signup. IANA public service.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_PLATFORM_NS = {
    "shopify": "Shopify",
    "myshopify": "Shopify",
    "wixdns": "Wix",
    "squarespace": "Squarespace",
    "bigcommerce": "BigCommerce",
    "wordpress": "WordPress",
    "cloudflare": "Cloudflare",
    "awsdns": "AWS",
    "googledomains": "Google",
    "domaincontrol": "GoDaddy",
    "registrar-servers": "Namecheap",
}


class RDAPWhoisCollector(Collector):
    """Enrich domains with RDAP registration data.

    Parameters:
        domains: list[str] of domains to look up. REQUIRED.
    """

    name = "rdap_whois"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("rdap_whois.no_domains")
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        async with httpx.AsyncClient(timeout=20.0) as client:
            for domain in domains:
                domain = domain.strip().lower().removeprefix("www.")
                if not domain:
                    continue

                try:
                    info = await _fetch_rdap(client, domain)
                    if not info:
                        continue

                    age_days = info.get("age_days")
                    age_label = _age_label(age_days)
                    platform = info.get("platform_hint", "unknown")

                    yield SignalEvent(
                        type="domain.registration",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=float(age_days) if age_days else 0.0,
                        value_text=f"{age_label} (registered {info.get('created', 'unknown')})",
                        payload={
                            "domain": domain,
                            "created": info.get("created"),
                            "expires": info.get("expires"),
                            "registrar": info.get("registrar"),
                            "nameservers": info.get("nameservers"),
                            "platform_hint": platform,
                            "age_days": age_days,
                            "age_label": age_label,
                        },
                        dedupe_key=f"rdap:{domain}:{day}",
                    )
                except Exception as exc:
                    log.debug("rdap_whois.lookup_failed", domain=domain, error=str(exc))


async def _fetch_rdap(client: httpx.AsyncClient, domain: str) -> dict[str, Any] | None:
    resp = await client.get(
        f"https://rdap.org/domain/{domain}",
        headers={"Accept": "application/rdap+json"},
        follow_redirects=True,
    )
    if resp.status_code != 200:
        return None

    data = resp.json()

    created = _extract_event_date(data, "registration")
    expires = _extract_event_date(data, "expiration")
    registrar = _extract_registrar(data)
    nameservers = _extract_nameservers(data)
    platform = _detect_platform(nameservers)

    age_days = None
    if created:
        try:
            created_dt = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (dt.datetime.now(dt.timezone.utc) - created_dt).days
        except (ValueError, TypeError):
            pass

    return {
        "created": created,
        "expires": expires,
        "registrar": registrar,
        "nameservers": nameservers,
        "platform_hint": platform,
        "age_days": age_days,
    }


def _extract_event_date(data: dict, action: str) -> str | None:
    for event in data.get("events", []):
        if event.get("eventAction") == action:
            return event.get("eventDate")
    return None


def _extract_registrar(data: dict) -> str | None:
    for entity in data.get("entities", []):
        roles = entity.get("roles", [])
        if "registrar" in roles:
            vcard = entity.get("vcardArray", [None, []])
            if len(vcard) > 1:
                for field in vcard[1]:
                    if field[0] == "fn":
                        return field[3]
    return None


def _extract_nameservers(data: dict) -> list[str]:
    ns_list = []
    for ns in data.get("nameservers", []):
        name = ns.get("ldhName", "").lower()
        if name:
            ns_list.append(name)
    return ns_list


def _detect_platform(nameservers: list[str]) -> str:
    for ns in nameservers:
        for pattern, platform in _PLATFORM_NS.items():
            if pattern in ns:
                return platform
    return "unknown"


def _age_label(age_days: int | None) -> str:
    if age_days is None:
        return "unknown_age"
    if age_days < 90:
        return "brand_new"
    if age_days < 365:
        return "young"
    if age_days < 1095:
        return "established"
    return "mature"
