"""Affiliate program detector — identifies brands with/without affiliate programs.

Checks multiple signals:
1. Common affiliate page paths (/affiliate, /partners, /referral, etc.)
2. Known affiliate network footprints in HTML (Impact, CJ, ShareASale, GoAffPro, etc.)
3. Affiliate meta tags and link patterns

Brands WITHOUT affiliate programs = opportunity for Grabon's affiliate marketing service.
Brands WITH programs = we capture their partner network details.

Free — just HTTP fetches via httpx. No API key needed.
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

_AFFILIATE_PATHS = [
    "/affiliate",
    "/affiliates",
    "/affiliate-program",
    "/affiliate-programme",
    "/partners",
    "/partner-program",
    "/referral",
    "/referral-program",
    "/refer",
    "/refer-and-earn",
    "/earn",
    "/become-a-partner",
    "/join-us",
]

_AFFILIATE_NETWORKS: list[tuple[str, re.Pattern[str]]] = [
    ("Impact", re.compile(r"impact\.com|impactradius\.com|impact-partner", re.I)),
    ("CJ Affiliate", re.compile(r"cj\.com|commission-junction|cjaffiliate", re.I)),
    ("ShareASale", re.compile(r"shareasale\.com|shareasale", re.I)),
    ("Awin", re.compile(r"awin\.com|awin1\.com|zanox", re.I)),
    ("Rakuten", re.compile(r"rakuten\.com|linksynergy|rakutenadvertising", re.I)),
    ("GoAffPro", re.compile(r"goaffpro\.com|goaffpro", re.I)),
    ("Refersion", re.compile(r"refersion\.com|refersion", re.I)),
    ("PartnerStack", re.compile(r"partnerstack\.com|partnerstack", re.I)),
    ("Tapfiliate", re.compile(r"tapfiliate\.com|tapfiliate", re.I)),
    ("LeadDyno", re.compile(r"leaddyno\.com|leaddyno", re.I)),
    ("Post Affiliate Pro", re.compile(r"postaffiliatepro\.com|qualityunit", re.I)),
    ("Everflow", re.compile(r"everflow\.io|everflow", re.I)),
    ("Affiliatly", re.compile(r"affiliatly\.com|affiliatly", re.I)),
    ("UpPromote", re.compile(r"uppromote\.com|uppromote", re.I)),
    ("Affiliate WP", re.compile(r"affiliatewp\.com|affiliatewp", re.I)),
    ("Admitad", re.compile(r"admitad\.com|admitad", re.I)),
    ("vCommission", re.compile(r"vcommission\.com|vcommission", re.I)),
    ("Cuelinks", re.compile(r"cuelinks\.com|cuelinks", re.I)),
    ("Optimise Media", re.compile(r"optimisemedia\.com|optimise", re.I)),
    ("Involve Asia", re.compile(r"involve\.asia|involveasia", re.I)),
]

_AFFILIATE_KEYWORDS = re.compile(
    r"affiliate\s+program|referral\s+program|earn\s+commission|"
    r"partner\s+with\s+us|become\s+an?\s+affiliate|"
    r"join\s+our\s+(affiliate|partner|referral)|"
    r"affiliate\s+dashboard|affiliate\s+signup|"
    r"commission\s+rate|referral\s+bonus|"
    r"earn\s+per\s+(sale|click|lead)",
    re.I,
)

_PARTNER_NAME_PATTERN = re.compile(
    r"(?:our\s+partners?|partnered\s+with|powered\s+by|"
    r"featured\s+(?:on|in)|as\s+seen\s+(?:on|in)|"
    r"trusted\s+by|supported\s+by|backed\s+by)",
    re.I,
)


class AffiliateProgramCollector(Collector):
    """Detect affiliate programs and partner networks for target domains.

    Parameters:
        domains: list[str] of domains to check. REQUIRED.
    """

    name = "affiliate_program"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("affiliate_program.no_domains")
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; GrabonIntelBot/0.2)"},
        ) as client:
            for domain in domains:
                domain = domain.strip().lower().removeprefix("www.")
                if not domain:
                    continue

                try:
                    result = await _analyze_affiliate(client, domain)
                    if result is None:
                        continue

                    has_program = result["has_program"]
                    score = result["score"]

                    yield SignalEvent(
                        type="affiliate.program",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=float(score),
                        value_text="has_affiliate" if has_program else "no_affiliate",
                        payload=result,
                        dedupe_key=f"affiliate:{domain}:{day}",
                    )
                except Exception as exc:
                    log.debug("affiliate_program.failed", domain=domain, error=str(exc))


async def _analyze_affiliate(
    client: httpx.AsyncClient, domain: str
) -> dict[str, Any] | None:
    """Check domain for affiliate program signals."""
    base_url = f"https://{domain}"
    has_program = False
    affiliate_pages_found: list[str] = []
    networks_detected: list[str] = []
    partners_mentioned: list[str] = []
    commission_details: str | None = None
    homepage_html: str | None = None

    # 1. Fetch homepage — check for affiliate network footprints
    try:
        resp = await client.get(base_url)
        if resp.status_code < 400:
            homepage_html = resp.text[:300_000]
            for name, pattern in _AFFILIATE_NETWORKS:
                if pattern.search(homepage_html):
                    networks_detected.append(name)
                    has_program = True
    except httpx.HTTPError:
        return None

    # 2. Check common affiliate page paths
    for path in _AFFILIATE_PATHS:
        try:
            resp = await client.get(f"{base_url}{path}", follow_redirects=True)
            if resp.status_code == 200:
                page_text = resp.text[:100_000]
                # Verify it's actually an affiliate page, not a redirect to homepage
                if _AFFILIATE_KEYWORDS.search(page_text):
                    affiliate_pages_found.append(path)
                    has_program = True

                    # Extract commission details if present
                    comm_match = re.search(
                        r"(\d+(?:\.\d+)?)\s*%\s*(?:commission|per\s+sale|earnings?)",
                        page_text, re.I,
                    )
                    if comm_match:
                        commission_details = f"{comm_match.group(1)}% commission"

                    # Check for network signups on affiliate page
                    for name, pattern in _AFFILIATE_NETWORKS:
                        if pattern.search(page_text) and name not in networks_detected:
                            networks_detected.append(name)

                    # Extract partner/brand names mentioned on the page
                    _extract_partners(page_text, partners_mentioned)

                    break  # Found an affiliate page, no need to check more paths
        except httpx.HTTPError:
            continue

    # 3. If no dedicated page, check homepage links for affiliate references
    if not has_program and homepage_html:
        link_pattern = re.compile(
            r'href=["\']([^"\']*(?:affiliate|referral|partner)[^"\']*)["\']',
            re.I,
        )
        matches = link_pattern.findall(homepage_html)
        if matches:
            affiliate_pages_found = [m[:200] for m in matches[:5]]
            has_program = True

    # 4. Check footer for partner logos/mentions
    if homepage_html:
        _extract_partners(homepage_html, partners_mentioned)

    # Compute opportunity score (higher = more opportunity for Grabon)
    score = _compute_affiliate_score(has_program, networks_detected, affiliate_pages_found)

    return {
        "domain": domain,
        "has_program": has_program,
        "affiliate_pages": affiliate_pages_found,
        "networks": sorted(set(networks_detected)),
        "partners": sorted(set(partners_mentioned))[:20],
        "commission_details": commission_details,
        "score": score,
    }


def _extract_partners(html: str, partners: list[str]) -> None:
    """Extract partner/brand names from HTML content."""
    # Look for partner sections
    if not _PARTNER_NAME_PATTERN.search(html):
        return
    # Extract image alt texts near partner mentions (common pattern: partner logo grids)
    alt_matches = re.findall(
        r'alt=["\']([A-Z][A-Za-z0-9 &.\-]{2,30})["\']',
        html,
    )
    for alt in alt_matches[:30]:
        cleaned = alt.strip()
        if 2 < len(cleaned) < 30 and not re.match(r"(logo|icon|image|banner|close|menu)", cleaned, re.I):
            if cleaned not in partners:
                partners.append(cleaned)


def _compute_affiliate_score(
    has_program: bool,
    networks: list[str],
    pages: list[str],
) -> int:
    """Score 0-100. HIGH = big opportunity for Grabon (no/weak affiliate program)."""
    if not has_program:
        return 90  # No affiliate at all = huge opportunity
    score = 30  # Has a program = less opportunity but can optimize
    if len(networks) == 0:
        score += 30  # DIY program without a network = room to improve
    if len(networks) == 1:
        score += 15  # Single network = expansion opportunity
    if not pages:
        score += 15  # Has network footprint but no dedicated page = poorly promoted
    return min(score, 100)
