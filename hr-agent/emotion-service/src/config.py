"""Emotion-service configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    log_level: str = "INFO"
    bind_host: str = "0.0.0.0"
    bind_port: int = 8090

    # Bearer token shared with the hiring-agent backend.
    emotion_api_key: str | None = None

    # HF model used for the emotion classifier head. Defaults to a public
    # wav2vec2 model fine-tuned on RAVDESS / SAVEE / IEMOCAP. Override with
    # a private model id + HF_TOKEN once you have a fine-tuned one.
    emotion_model_id: str = "ehcalabres/wav2vec2-lg-xlsr-en-speech-emotion-recognition"
    hf_token: str | None = None

    # Cap audio download size to prevent abuse.
    max_audio_mb: int = 60

    # Cap inference runtime per request.
    max_audio_seconds: float = 1800.0  # 30 minutes


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
