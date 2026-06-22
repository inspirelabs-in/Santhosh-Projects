"""[TO BE REMOVED] Stub: emotion-service client.

Dead since voice scoring moved to Gemini (services/gemini_audio_eval.py), which
evaluates audio + emotion inline. No live caller -- safe to delete with the
EmotionFeatures model once confirmed nothing imports it.
"""

from __future__ import annotations

from src.models.v1 import EmotionFeatures


async def analyze_recording(
    *, audio_url: str, skip_emotion: bool = False
) -> EmotionFeatures | None:
    return None
