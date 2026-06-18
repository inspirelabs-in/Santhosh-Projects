"""Stub: emotion-service client (disabled — replaced by Gemini audio eval)."""

from __future__ import annotations

from src.models.v1 import EmotionFeatures


async def analyze_recording(
    *, audio_url: str, skip_emotion: bool = False
) -> EmotionFeatures | None:
    return None
