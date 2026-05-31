"""RDAP — successor to WHOIS. Free, JSON, no key.

We use RDAP Bootstrap to find the right server per TLD, then query that
server. Returned data lets us read domain creation date (≈ brand age
proxy) and registrar.

Spec: https://www.icann.org/resources/pages/rdap-2017-09-22-en
Bootstrap: https://data.iana.org/rdap/dns.json
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

from .base import Tool, ToolError, ToolResult


_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
_BOOTSTRAP: dict[str, list[str]] = {}


async def _bootstrap(client: httpx.AsyncClient) -> None:
    global _BOOTSTRAP
    if _BOOTSTRAP:
        return
    r = await client.get(_BOOTSTRAP_URL)
    r.raise_for_status()
    data = r.json()
    for tlds, urls in data.get("services", []):
        for t in tlds:
            _BOOTSTRAP[t.lower()] = list(urls)


class RDAPTool(Tool):
    name = "rdap"

    def __init__(self) -> None:
        super().__init__(rate=3, period=1.0)

    @property
    def available(self) -> bool:
        return True

    async def call(self, *, domain: str) -> ToolResult:
        domain = domain.strip().lower().removeprefix("www.")
        if "." not in domain:
            raise ToolError("invalid domain")
        tld = domain.rsplit(".", 1)[-1]
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            await _bootstrap(c)
            servers = _BOOTSTRAP.get(tld)
            if not servers:
                raise ToolError(f"no RDAP server for .{tld}")
            last_err: str = ""
            for base in servers:
                try:
                    r = await c.get(f"{base.rstrip('/')}/domain/{domain}")
                    if r.status_code == 404:
                        return ToolResult(
                            name=self.name, cost_cents=0, data={"domain": domain, "found": False}
                        )
                    if r.status_code >= 400:
                        last_err = f"{base}: {r.status_code}"
                        continue
                    data = r.json()
                    return ToolResult(
                        name=self.name,
                        cost_cents=0,
                        data=_extract(domain, data),
                    )
                except httpx.HTTPError as exc:
                    last_err = f"{base}: {exc}"
                    continue
        raise ToolError(last_err or "rdap_unreachable")


def _extract(domain: str, data: dict[str, Any]) -> dict[str, Any]:
    created = None
    expires = None
    registrar = None
    for ev in data.get("events") or []:
        action = (ev.get("eventAction") or "").lower()
        if action in {"registration", "creation"} and not created:
            created = ev.get("eventDate")
        if action == "expiration" and not expires:
            expires = ev.get("eventDate")
    for ent in data.get("entities") or []:
        roles = [r.lower() for r in ent.get("roles") or []]
        if "registrar" in roles:
            v = ent.get("vcardArray")
            if isinstance(v, list) and len(v) > 1:
                for field in v[1]:
                    if isinstance(field, list) and field[0] == "fn" and len(field) > 3:
                        registrar = field[3]
                        break
            if not registrar:
                registrar = ent.get("handle")
            break
    age_days = None
    if created:
        try:
            ds = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (dt.datetime.now(dt.timezone.utc) - ds).days
        except ValueError:
            pass
    return {
        "domain": domain,
        "found": True,
        "created_on": (created or "")[:10],
        "expires_on": (expires or "")[:10],
        "registrar": registrar,
        "age_days": age_days,
    }
