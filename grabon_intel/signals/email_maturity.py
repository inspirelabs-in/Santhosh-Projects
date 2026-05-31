"""Email maturity collector — DNS-based email infrastructure analysis.

Checks MX records, SPF, DKIM, DMARC for target domains to assess
email marketing maturity. Free — uses only DNS lookups via `dnspython`.

Brands with no SPF/DMARC = weak email hygiene = opportunity for
Grabon's email marketing services.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_KNOWN_ESPS = {
    "google.com": "Google Workspace",
    "googlemail.com": "Google Workspace",
    "outlook.com": "Microsoft 365",
    "protection.outlook.com": "Microsoft 365",
    "pphosted.com": "Proofpoint",
    "mimecast.com": "Mimecast",
    "messagelabs.com": "Symantec",
    "sendgrid.net": "SendGrid",
    "mailgun.org": "Mailgun",
    "mandrillapp.com": "Mailchimp/Mandrill",
    "sparkpostmail.com": "SparkPost",
    "amazonses.com": "Amazon SES",
    "mailchimp.com": "Mailchimp",
    "freshdesk.com": "Freshworks",
    "zendesk.com": "Zendesk",
    "hubspot.com": "HubSpot",
    "klaviyo.com": "Klaviyo",
    "braze.com": "Braze",
    "iterable.com": "Iterable",
    "clevertap.com": "CleverTap",
    "moengage.com": "MoEngage",
    "webengage.com": "WebEngage",
    "netcorecloud.com": "Netcore",
}


class EmailMaturityCollector(Collector):
    """Check email infrastructure maturity via DNS.

    Parameters:
        domains: list[str] of domains to check. REQUIRED.
    """

    name = "email_maturity"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("email_maturity.no_domains")
            return

        try:
            import dns.asyncresolver
        except ImportError:
            log.warning("email_maturity.dnspython_not_installed — pip install dnspython")
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()
        resolver = dns.asyncresolver.Resolver()
        resolver.lifetime = 5.0

        for domain in domains:
            domain = domain.strip().lower().removeprefix("www.")
            if not domain:
                continue

            try:
                result = await _check_email_infra(resolver, domain)
                score = _compute_maturity_score(result)

                yield SignalEvent(
                    type="email.maturity",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=float(score),
                    value_text=result["maturity_level"],
                    payload={
                        "domain": domain,
                        "has_mx": result["has_mx"],
                        "mx_providers": result["mx_providers"],
                        "has_spf": result["has_spf"],
                        "spf_includes": result["spf_includes"],
                        "has_dmarc": result["has_dmarc"],
                        "dmarc_policy": result["dmarc_policy"],
                        "has_dkim": result["has_dkim"],
                        "email_providers": sorted(result["email_providers"]),
                        "maturity_score": score,
                        "maturity_level": result["maturity_level"],
                    },
                    dedupe_key=f"email:{domain}:{day}",
                )
            except Exception as exc:
                log.debug("email_maturity.check_failed", domain=domain, error=str(exc))


async def _check_email_infra(resolver, domain: str) -> dict[str, Any]:
    import dns.asyncresolver

    result: dict[str, Any] = {
        "has_mx": False,
        "mx_providers": [],
        "has_spf": False,
        "spf_includes": [],
        "has_dmarc": False,
        "dmarc_policy": None,
        "has_dkim": False,
        "email_providers": set(),
        "maturity_level": "none",
    }

    # MX records
    try:
        mx_answer = await resolver.resolve(domain, "MX")
        mx_hosts = [str(r.exchange).rstrip(".").lower() for r in mx_answer]
        result["has_mx"] = len(mx_hosts) > 0
        result["mx_providers"] = mx_hosts[:5]
        for host in mx_hosts:
            for pattern, provider in _KNOWN_ESPS.items():
                if pattern in host:
                    result["email_providers"].add(provider)
    except (dns.asyncresolver.NXDOMAIN, dns.asyncresolver.NoAnswer, dns.asyncresolver.NoNameservers, Exception):
        pass

    # SPF (TXT record)
    try:
        txt_answer = await resolver.resolve(domain, "TXT")
        for r in txt_answer:
            txt_val = r.to_text().strip('"')
            if txt_val.startswith("v=spf1"):
                result["has_spf"] = True
                includes = re.findall(r"include:(\S+)", txt_val)
                result["spf_includes"] = includes[:10]
                for inc in includes:
                    for pattern, provider in _KNOWN_ESPS.items():
                        if pattern in inc:
                            result["email_providers"].add(provider)
    except Exception:
        pass

    # DMARC
    try:
        dmarc_answer = await resolver.resolve(f"_dmarc.{domain}", "TXT")
        for r in dmarc_answer:
            txt_val = r.to_text().strip('"')
            if txt_val.startswith("v=DMARC1"):
                result["has_dmarc"] = True
                policy_match = re.search(r"p=(\w+)", txt_val)
                if policy_match:
                    result["dmarc_policy"] = policy_match.group(1)
    except Exception:
        pass

    # DKIM (check common selectors)
    for selector in ["google", "selector1", "default", "k1", "s1", "mail"]:
        try:
            await resolver.resolve(f"{selector}._domainkey.{domain}", "TXT")
            result["has_dkim"] = True
            break
        except Exception:
            continue

    result["email_providers"] = sorted(result["email_providers"])
    result["maturity_level"] = _classify_maturity(result)
    return result


def _compute_maturity_score(result: dict[str, Any]) -> int:
    score = 0
    if result["has_mx"]:
        score += 20
    if result["has_spf"]:
        score += 25
    if result["has_dmarc"]:
        score += 25
        if result["dmarc_policy"] in ("reject", "quarantine"):
            score += 10
    if result["has_dkim"]:
        score += 20
    return score


def _classify_maturity(result: dict[str, Any]) -> str:
    score = _compute_maturity_score(result)
    if score >= 80:
        return "advanced"
    if score >= 50:
        return "moderate"
    if score >= 20:
        return "basic"
    return "none"
