"""Audio feature extractors -- paralinguistic + categorical emotion.

Two layers:

  * Paralinguistic (rule-based, librosa): pitch mean/variance, speaking
    rate (words per minute proxy via syllable detection), pause ratio,
    arousal/valence proxies. Always available, runs in <1s on a 5-min clip.

  * Categorical emotion (ML, transformers): a wav2vec2 classifier head
    trained on RAVDESS-class labels (angry / calm / disgust / fearful /
    happy / sad / surprised / neutral). Loaded lazily.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import librosa
import numpy as np
import soundfile as sf

from src.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paralinguistic features
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Paralinguistic:
    avg_pitch_hz: float | None
    pitch_variance: float | None
    speaking_rate_wpm: float | None
    pause_ratio: float | None
    arousal_score: float | None
    valence_score: float | None
    confidence_score: float | None


def _safe_float(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return v


def extract_paralinguistic(audio: np.ndarray, sr: int) -> Paralinguistic:
    if audio.size == 0:
        return Paralinguistic(None, None, None, None, None, None, None)

    # Convert to mono if needed.
    if audio.ndim > 1:
        audio = librosa.to_mono(audio.T if audio.shape[0] < audio.shape[1] else audio)

    # Pitch: pyin returns f0 estimate per frame (Hz) with NaN where unvoiced.
    try:
        f0, voiced_flag, _ = librosa.pyin(
            audio,
            fmin=float(librosa.note_to_hz("C2")),  # ~65 Hz
            fmax=float(librosa.note_to_hz("C6")),  # ~1046 Hz
            sr=sr,
        )
        f0_voiced = f0[~np.isnan(f0)] if f0 is not None else np.array([])
        avg_pitch = float(f0_voiced.mean()) if f0_voiced.size > 0 else None
        pitch_var = float(f0_voiced.var()) if f0_voiced.size > 0 else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("pitch extraction failed: %s", exc)
        avg_pitch, pitch_var = None, None

    # Pause ratio = fraction of frames below an RMS energy threshold.
    rms = librosa.feature.rms(y=audio).flatten()
    rms_norm = rms / (rms.max() + 1e-9)
    pause_frames = int((rms_norm < 0.1).sum())
    pause_ratio = float(pause_frames / max(rms.size, 1)) if rms.size else None

    # Speaking rate proxy: detected syllable peaks per minute.
    try:
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr)
        peaks = librosa.util.peak_pick(
            onset_env,
            pre_max=3,
            post_max=3,
            pre_avg=3,
            post_avg=5,
            delta=0.5,
            wait=10,
        )
        duration_min = max(audio.size / sr / 60.0, 1e-9)
        # ~1.5 syllables per word average -> WPM proxy.
        wpm_proxy = float(len(peaks) / 1.5 / duration_min)
    except Exception:  # noqa: BLE001
        wpm_proxy = None

    # Arousal proxy: high-frequency energy ratio + RMS variance.
    try:
        spectral_centroid = librosa.feature.spectral_centroid(y=audio, sr=sr).mean()
        arousal = float(np.tanh((spectral_centroid - 1500) / 1500))
        # Map [-1, 1] -> [0, 1]
        arousal = (arousal + 1) / 2
    except Exception:  # noqa: BLE001
        arousal = None

    # Valence proxy: low pitch variance + low pause ratio = calm/positive.
    valence: float | None = None
    if pitch_var is not None and pause_ratio is not None:
        valence_raw = 1.0 - min(pitch_var / 5000, 1.0) * 0.5 - pause_ratio * 0.5
        valence = float(max(0.0, min(1.0, valence_raw)))

    # Confidence proxy: low pause ratio + steady pitch + reasonable WPM.
    confidence: float | None = None
    if pause_ratio is not None:
        confidence = max(0.0, 1.0 - pause_ratio * 1.5)
        if wpm_proxy is not None:
            # Penalise both too-slow and too-fast speech.
            wpm_penalty = abs(wpm_proxy - 130) / 130
            confidence = max(0.0, confidence - 0.3 * min(wpm_penalty, 1.0))
        confidence = float(min(1.0, confidence))

    return Paralinguistic(
        avg_pitch_hz=_safe_float(avg_pitch),
        pitch_variance=_safe_float(pitch_var),
        speaking_rate_wpm=_safe_float(wpm_proxy),
        pause_ratio=_safe_float(pause_ratio),
        arousal_score=_safe_float(arousal),
        valence_score=_safe_float(valence),
        confidence_score=_safe_float(confidence),
    )


# ---------------------------------------------------------------------------
# Categorical emotion (lazy-loaded HF model)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _load_emotion_model():
    from transformers import (
        AutoFeatureExtractor,
        AutoModelForAudioClassification,
    )

    settings = get_settings()
    kwargs: dict[str, Any] = {}
    if settings.hf_token:
        kwargs["token"] = settings.hf_token
    feat = AutoFeatureExtractor.from_pretrained(settings.emotion_model_id, **kwargs)
    model = AutoModelForAudioClassification.from_pretrained(
        settings.emotion_model_id, **kwargs
    )
    model.eval()
    return feat, model


def predict_emotion(audio: np.ndarray, sr: int) -> tuple[str, float]:
    """Return (label, probability). Falls back to ('neutral', 0.0) on error."""

    import torch  # imported here so service starts even on CPU-only boxes

    try:
        feat_extractor, model = _load_emotion_model()
    except Exception as exc:  # noqa: BLE001
        logger.warning("emotion model load failed: %s", exc)
        return "neutral", 0.0

    if sr != 16_000:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=16_000)
        sr = 16_000

    # Cap to first 30s for inference latency.
    audio_clip = audio[: 30 * sr]

    try:
        inputs = feat_extractor(
            audio_clip,
            sampling_rate=sr,
            return_tensors="pt",
            padding=True,
        )
        with torch.no_grad():
            logits = model(**inputs).logits[0]
        probs = torch.softmax(logits, dim=-1)
        idx = int(probs.argmax().item())
        return model.config.id2label[idx], float(probs[idx].item())
    except Exception as exc:  # noqa: BLE001
        logger.exception("emotion inference failed: %s", exc)
        return "neutral", 0.0


# ---------------------------------------------------------------------------
# Decoder helper -- accepts raw bytes (from URL or upload), returns (audio, sr)
# ---------------------------------------------------------------------------


def decode_audio(blob: bytes) -> tuple[np.ndarray, int]:
    try:
        audio, sr = sf.read(io.BytesIO(blob), dtype="float32")
    except Exception:
        # Fallback: librosa handles more formats (mp3 via audioread / ffmpeg).
        audio, sr = librosa.load(io.BytesIO(blob), sr=None, mono=True)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return np.asarray(audio, dtype=np.float32), int(sr)
