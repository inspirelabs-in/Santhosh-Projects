"""HubSpot CRM connector (free tier).

Syncs qualified leads → HubSpot contacts/deals. Uses the free HubSpot
CRM API (v3) with a private app token.

Free tier limits: 250K contacts, 100 API calls/10s.
"""
from __future__ import annotations

import hashlib
from typing import Any

import httpx

from ..config import get_settings
from ..logging import get_logger

log = get_logger(__name__)

_BASE = "https://api.hubapi.com"


class HubSpotConnector:
    """Sync brands/dossiers to HubSpot CRM."""

    def __init__(self) -> None:
        s = get_settings()
        self._token = s.hubspot_api_key
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return bool(self._token)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=_BASE,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
        return self._client

    async def upsert_company(self, *, domain: str, name: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create or update a HubSpot company by domain."""
        if not self.available:
            return {"error": "hubspot_not_configured"}

        client = await self._get_client()
        props = {
            "domain": domain,
            "name": name,
            **(properties or {}),
        }

        # Search by domain first
        search_resp = await client.post(
            "/crm/v3/objects/companies/search",
            json={
                "filterGroups": [{
                    "filters": [{"propertyName": "domain", "operator": "EQ", "value": domain}]
                }],
                "limit": 1,
            },
        )

        if search_resp.status_code == 200:
            results = search_resp.json().get("results", [])
            if results:
                company_id = results[0]["id"]
                update_resp = await client.patch(
                    f"/crm/v3/objects/companies/{company_id}",
                    json={"properties": props},
                )
                log.info("hubspot.company_updated", company_id=company_id, domain=domain)
                return update_resp.json() if update_resp.status_code == 200 else {"error": update_resp.text}

        create_resp = await client.post(
            "/crm/v3/objects/companies",
            json={"properties": props},
        )
        if create_resp.status_code in (200, 201):
            data = create_resp.json()
            log.info("hubspot.company_created", company_id=data.get("id"), domain=domain)
            return data
        return {"error": create_resp.text[:300]}

    async def create_deal(
        self,
        *,
        company_id: str,
        deal_name: str,
        pipeline: str = "default",
        stage: str = "appointmentscheduled",
        amount: int | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a deal associated with a company."""
        if not self.available:
            return {"error": "hubspot_not_configured"}

        client = await self._get_client()
        props = {
            "dealname": deal_name,
            "pipeline": pipeline,
            "dealstage": stage,
            **({"amount": str(amount)} if amount else {}),
            **(properties or {}),
        }

        resp = await client.post(
            "/crm/v3/objects/deals",
            json={
                "properties": props,
                "associations": [{
                    "to": {"id": company_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 342}],
                }],
            },
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            log.info("hubspot.deal_created", deal_id=data.get("id"), deal_name=deal_name)
            return data
        return {"error": resp.text[:300]}

    async def sync_dossier(self, *, brand_id: int, domain: str, name: str, score: dict, opportunity: dict) -> dict[str, Any]:
        """Full sync: upsert company + create deal if score is hot/warm."""
        tier = score.get("tier", "park")
        total = score.get("total", 0)

        services = [s.get("service", "") for s in (opportunity.get("services_recommended") or [])]
        deal_size = opportunity.get("estimated_deal_size_inr") or score.get("estimated_deal_value_inr")

        company = await self.upsert_company(
            domain=domain,
            name=name,
            properties={
                "grabon_score": str(total),
                "grabon_tier": tier,
                "grabon_services": ", ".join(services[:5]),
                "grabon_brand_id": str(brand_id),
            },
        )

        if company.get("error"):
            return company

        company_id = company.get("id")
        result = {"company": company}

        if tier in ("hot", "warm") and company_id:
            deal = await self.create_deal(
                company_id=company_id,
                deal_name=f"Grabon × {name}",
                amount=deal_size,
                properties={
                    "grabon_score": str(total),
                    "grabon_services": ", ".join(services[:5]),
                },
            )
            result["deal"] = deal

        return result

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
