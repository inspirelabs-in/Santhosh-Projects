"""Trigger Temporal workflows from HTTP."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from temporalio.client import Client, TLSConfig

from ...config import get_settings

router = APIRouter(prefix="/workflows", tags=["workflows"])


class TriggerDiscovery(BaseModel):
    collector: str
    params: dict[str, Any] = {}
    fan_out: bool = False
    max_fan_out: int = 10


class TriggerDossier(BaseModel):
    brand_id: int
    reason: str = "manual"
    brand_hint: dict[str, Any] = {}


async def _client() -> Client:
    s = get_settings()
    return await Client.connect(
        s.temporal_address,
        namespace=s.temporal_namespace,
        tls=TLSConfig() if s.temporal_tls else False,
    )


@router.post("/discovery")
async def trigger_discovery(body: TriggerDiscovery) -> dict:
    # Defer workflow class imports — these pull LangGraph etc. Keep route module light.
    from ...workflows import DiscoveryWF, DiscoveryWFInput

    s = get_settings()
    try:
        client = await _client()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"temporal unavailable: {exc}") from exc

    handle = await client.start_workflow(
        DiscoveryWF.run,
        DiscoveryWFInput(
            collector=body.collector,
            params=body.params,
            fan_out_dossiers=body.fan_out,
            max_fan_out=body.max_fan_out,
        ),
        id=f"discovery-{body.collector}-{__import__('time').time_ns()}",
        task_queue=s.temporal_task_queue,
    )
    return {"workflow_id": handle.id, "run_id": handle.first_execution_run_id}


@router.post("/dossier")
async def trigger_dossier(body: TriggerDossier) -> dict:
    from ...workflows import DossierWF, DossierWFInput

    s = get_settings()
    try:
        client = await _client()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"temporal unavailable: {exc}") from exc

    handle = await client.start_workflow(
        DossierWF.run,
        DossierWFInput(brand_id=body.brand_id, reason=body.reason, brand_hint=body.brand_hint),
        id=f"dossier-{body.brand_id}-{__import__('time').time_ns()}",
        task_queue=s.temporal_task_queue,
    )
    return {"workflow_id": handle.id, "run_id": handle.first_execution_run_id}
