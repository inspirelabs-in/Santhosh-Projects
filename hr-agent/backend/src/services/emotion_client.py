"""HTTP client for the local emotion-service microservice.

Used by the voice + meeting analyzer activities to enrich a recording with
paralinguistic features. The service is optional -- when
``settings.emotion_model_endpoint`` is unset, ``analyze_recording`` returns
``None`` and the LLM evaluator runs without paralinguistic context.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config import get_settings
from src.models.v1 import EmotionFeatures

logger = logging.getLogger(__name__)


async def analyze_recording(
    *, audio_url: str, skip_emotion: bool = False
) -> EmotionFeatures | None:
    settings = get_settings()
    endpoint = settings.emotion_model_endpoint
    if not endpoint or settings.emotion_model_provider == "disabled":
        return None

    headers = {"content-type": "application/json"}
    if settings.emotion_api_key:
        headers["authorization"] = f"Bearer {settings.emotion_api_key}"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{endpoint.rstrip('/')}/analyze",
                json={"audio_url": audio_url, "skip_emotion": skip_emotion},
                headers=headers,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("emotion-service call failed: %s", exc)
        return None

    para = data.get("paralinguistic") or {}
    try:
        return EmotionFeatures.model_validate(para)
    except Exception as exc:  # noqa: BLE001
        logger.warning("emotion-service payload invalid: %s", exc)
        return None
