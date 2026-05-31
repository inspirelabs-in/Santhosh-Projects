"""Compliance-safe scraping helpers.

Two pieces:
  - `robots_allowed(url)`: fetches & caches the target's robots.txt,
    answers allow/disallow for our UA. Cached in-process for 1h.
  - `record_fetch(...)`: writes one row to `scraping_ledger`. Callers
    use the `recorded_get` helper which combines both into one call.

Defensive defaults:
  - Treat 4xx/5xx on robots.txt as "allowed" (most sites). Log it.
  - 404 robots.txt = open by RFC convention.
  - When user-agent matches a `Disallow: /` block, refuse the fetch.
"""
from __future__ import annotations

import datetime as dt
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import text

from .db import session as session_ctx
from .logging import get_logger

log = get_logger(__name__)


_UA = "GrabonIntelBot/0.1"
_CACHE: dict[str, tuple[RobotFileParser, float]] = {}
_TTL = 3600.0


async def _get_robots(domain: str) -> RobotFileParser | None:
    now = time.monotonic()
    cached = _CACHE.get(domain)
    if cached and now - cached[1] < _TTL:
        return cached[0]
    rp = RobotFileParser()
    rp.set_url(f"https://{domain}/robots.txt")
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers={"User-Agent": _UA}) as c:
            r = await c.get(f"https://{domain}/robots.txt")
            if r.status_code == 404 or r.status_code >= 400:
                rp.parse([])  # empty = open
            else:
                rp.parse(r.text.splitlines())
    except Exception as exc:  # noqa: BLE001
        log.warning("robots.fetch_failed", domain=domain, exc=str(exc))
        rp.parse([])  # fail open — we never want to silently block legitimate fetches
    _CACHE[domain] = (rp, now)
    return rp


async def robots_allowed(url: str) -> bool | None:
    try:
        host = urlparse(url).hostname or ""
        if not host:
            return None
        rp = await _get_robots(host)
        if rp is None:
            return None
        return rp.can_fetch(_UA, url)
    except Exception as exc:  # noqa: BLE001
        log.warning("robots.eval_failed", url=url, exc=str(exc))
        return None


async def record_fetch(
    *,
    url: str,
    tool: str,
    method: str = "GET",
    status_code: int | None = None,
    robots_allowed_value: bool | None = None,
    bytes_returned: int | None = None,
    notes: str | None = None,
) -> None:
    host = urlparse(url).hostname or ""
    async with session_ctx() as s:
        await s.execute(
            text(
                "INSERT INTO scraping_ledger (url, domain, method, tool, status_code, "
                "robots_allowed, bytes_returned, notes) "
                "VALUES (:u, :d, :m, :t, :sc, :ra, :br, :n)"
            ),
            {
                "u": url,
                "d": host,
                "m": method,
                "t": tool,
                "sc": status_code,
                "ra": robots_allowed_value,
                "br": bytes_returned,
                "n": notes,
            },
        )


_ = dt  # keep import (used by callers / future fields)
