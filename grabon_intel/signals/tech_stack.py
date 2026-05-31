"""Tech stack detection via HTTP response analysis.

Analyzes target domains by fetching their homepage and checking
HTML/headers for known ecommerce platforms, payment gateways,
and analytics tools. Confirms D2C brands running their own storefront.

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

_TECH_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("Shopify", "ecommerce", re.compile(r"cdn\.shopify\.com|Shopify\.theme|myshopify\.com", re.I)),
    ("WooCommerce", "ecommerce", re.compile(r"woocommerce|wc-block|wp-content.*woo", re.I)),
    ("Magento", "ecommerce", re.compile(r"mage/cookies|Magento_Ui|magento\.com", re.I)),
    ("BigCommerce", "ecommerce", re.compile(r"bigcommerce\.com|cdn\d+\.bigcommerce", re.I)),
    ("PrestaShop", "ecommerce", re.compile(r"prestashop|presta\.js", re.I)),
    ("OpenCart", "ecommerce", re.compile(r"catalog/view|route=common/home", re.I)),
    ("Wix", "ecommerce", re.compile(r"wix\.com|wixstatic\.com|_wix_browser_sess", re.I)),
    ("Dukaan", "ecommerce", re.compile(r"mydukaan\.io|dukaan\.com", re.I)),
    ("Razorpay", "payment", re.compile(r"razorpay\.com|Razorpay", re.I)),
    ("Cashfree", "payment", re.compile(r"cashfree\.com|cashfree", re.I)),
    ("PayU", "payment", re.compile(r"payu\.in|payubiz", re.I)),
    ("Stripe", "payment", re.compile(r"stripe\.com|js\.stripe\.com", re.I)),
    ("PayPal", "payment", re.compile(r"paypal\.com|paypalobjects", re.I)),
    ("Paytm", "payment", re.compile(r"paytm\.com|paytm\.min\.js", re.I)),
    ("CCAvenue", "payment", re.compile(r"ccavenue\.com|ccavenue", re.I)),
    ("Google Analytics", "analytics", re.compile(r"google-analytics\.com|gtag/js|UA-\d+|G-[A-Z0-9]+", re.I)),
    ("Google Tag Manager", "analytics", re.compile(r"googletagmanager\.com|GTM-[A-Z0-9]+", re.I)),
    ("Facebook Pixel", "analytics", re.compile(r"connect\.facebook\.net/en_US/fbevents|fbq\(", re.I)),
    ("Hotjar", "analytics", re.compile(r"hotjar\.com|_hjSettings", re.I)),
    ("Mixpanel", "analytics", re.compile(r"mixpanel\.com|mixpanel\.init", re.I)),
    ("Clevertap", "analytics", re.compile(r"clevertap\.com|clevertap", re.I)),
    ("MoEngage", "analytics", re.compile(r"moengage\.com|moengage", re.I)),
    ("WebEngage", "analytics", re.compile(r"webengage\.com|webengage", re.I)),
    ("Klaviyo", "marketing", re.compile(r"klaviyo\.com|klaviyo", re.I)),
    ("Mailchimp", "marketing", re.compile(r"mailchimp\.com|list-manage\.com|mc\.js", re.I)),
    ("HubSpot", "marketing", re.compile(r"hubspot\.com|hs-scripts|hbspt", re.I)),
    ("Freshdesk", "support", re.compile(r"freshdesk\.com|freshchat", re.I)),
    ("Zendesk", "support", re.compile(r"zendesk\.com|zdassets\.com", re.I)),
    ("Intercom", "support", re.compile(r"intercom\.io|intercomcdn", re.I)),
    ("WordPress", "cms", re.compile(r"wp-content|wp-includes|wordpress", re.I)),
    ("Next.js", "framework", re.compile(r"_next/static|__next|nextjs", re.I)),
    ("React", "framework", re.compile(r"react\.production|reactDOM|__REACT", re.I)),
    ("Laravel", "framework", re.compile(r"laravel|XSRF-TOKEN.*laravel_session", re.I)),
]

_D2C_CATEGORIES = {"ecommerce", "payment"}
_ANALYTICS_CATEGORIES = {"analytics", "marketing"}


class TechStackCollector(Collector):
    """Detect tech stack for a list of domains.

    Parameters:
        domains: list[str] of domains to check. REQUIRED.
    """

    name = "tech_stack"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("tech_stack.no_domains")
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
                    techs = await _detect_tech(client, domain)
                    if not techs:
                        continue

                    tech_names = {t[0] for t in techs}
                    tech_categories = {t[1] for t in techs}

                    d2c_platforms = {t[0] for t in techs if t[1] == "ecommerce"}
                    payments = {t[0] for t in techs if t[1] == "payment"}
                    analytics = {t[0] for t in techs if t[1] in _ANALYTICS_CATEGORIES}

                    is_d2c = bool(d2c_platforms or payments)
                    score = _compute_score(d2c_platforms, payments, analytics, tech_names)

                    yield SignalEvent(
                        type="tech.stack",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=float(score),
                        value_text="d2c_confirmed" if is_d2c else "non_d2c",
                        payload={
                            "domain": domain,
                            "all_technologies": sorted(tech_names),
                            "d2c_platforms": sorted(d2c_platforms),
                            "payment_gateways": sorted(payments),
                            "analytics": sorted(analytics),
                            "categories": sorted(tech_categories),
                            "is_d2c": is_d2c,
                            "tech_count": len(tech_names),
                            "score": score,
                        },
                        dedupe_key=f"tech:{domain}:{day}",
                    )
                except Exception as exc:
                    log.debug("tech_stack.analyze_failed", domain=domain, error=str(exc))


async def _detect_tech(client: httpx.AsyncClient, domain: str) -> list[tuple[str, str]]:
    """Fetch homepage and match against known tech patterns."""
    for scheme in ("https", "http"):
        try:
            resp = await client.get(f"{scheme}://{domain}")
            if resp.status_code >= 400:
                continue

            body = resp.text[:200_000]
            headers_str = str(resp.headers)
            content = body + "\n" + headers_str

            found = []
            for name, category, pattern in _TECH_PATTERNS:
                if pattern.search(content):
                    found.append((name, category))
            return found
        except (httpx.HTTPError, httpx.InvalidURL):
            continue
    return []


def _compute_score(
    d2c: set[str], payments: set[str], analytics: set[str], all_tech: set[str]
) -> int:
    score = 0
    if d2c:
        score += 40
    if payments:
        score += 30
    if analytics:
        score += 20
    if len(all_tech) >= 5:
        score += 10
    return score
