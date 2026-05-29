"""Shared Temporal client factory (used by API layer to start workflows)."""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client

from src.config import get_settings

logger = logging.getLogger(__name__)

_client: Client | None = None
_lock = asyncio.Lock()


async def get_temporal_client() -> Client:
    global _client
    if _client is not None:
        return _client
    async with _lock:
        if _client is None:
            settings = get_settings()
            _client = await Client.connect(
                settings.temporal_address, namespace=settings.temporal_namespace
            )
            logger.info("Temporal client connected (%s)", settings.temporal_address)
    return _client
