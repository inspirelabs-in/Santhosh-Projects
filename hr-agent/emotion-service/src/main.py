"""Emotion microservice HTTP API.

  POST /analyze  body = {"audio_url": "...", "max_seconds": 1800}
                 -> { paralinguistic: {...}, emotion: {label, confidence} }

The hiring-agent backend calls this with a presigned R2 URL after a voice
call ends, then attaches the result to ``voice_calls.emotion_features``.

The bearer token is the only auth -- networking should keep this service
internal.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.config import get_settings
from src.features import decode_audio, extract_paralinguistic, predict_emotion

logger = logging.getLogger(__name__)
_settings = get_settings()


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    expected = _settings.emotion_api_key
    if not expected:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    if authorization.split(" ", 1)[1].strip() != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(
        level=_settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("emotion-service starting on %s:%d", _settings.bind_host, _settings.bind_port)
    yield


app = FastAPI(title="Hiring Agent Emotion Service", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "model": _settings.emotion_model_id})


class AnalyzeBody(BaseModel):
    audio_url: str = Field(description="HTTPS URL the service can fetch")
    skip_emotion: bool = False


@app.post("/analyze", dependencies=[Depends(require_api_key)])
async def analyze(body: AnalyzeBody) -> dict[str, Any]:
    settings = get_settings()
    max_bytes = settings.max_audio_mb * 1024 * 1024

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(body.audio_url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"audio fetch failed: {exc}",
        )

    blob = resp.content
    if len(blob) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"audio too large: {len(blob)} bytes (limit {max_bytes})",
        )

    audio, sr = await asyncio.to_thread(decode_audio, blob)
    duration_sec = float(audio.size / sr) if sr else 0.0
    if duration_sec > settings.max_audio_seconds:
        # Trim rather than reject; long meetings should still get a feature row.
        audio = audio[: int(settings.max_audio_seconds * sr)]

    paralinguistic = await asyncio.to_thread(extract_paralinguistic, audio, sr)

    if body.skip_emotion:
        emotion_label, emotion_conf = "neutral", 0.0
    else:
        emotion_label, emotion_conf = await asyncio.to_thread(predict_emotion, audio, sr)

    return {
        "duration_sec": duration_sec,
        "paralinguistic": {
            **asdict(paralinguistic),
            "dominant_emotion": emotion_label,
        },
        "emotion": {
            "label": emotion_label,
            "confidence": emotion_conf,
        },
    }
