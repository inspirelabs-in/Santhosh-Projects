"""Unified Gemini 2.5 Flash evaluator for voice-screen recordings.

Gemini receives the audio file (primary) + transcript (secondary) in one call
and returns a complete VoiceCallScore — no separate paralinguistic step.

Audio is the primary signal: tone, English clarity, confidence, engagement.
Transcript is secondary: used to verify content, extract logistics facts,
and catch claims the audio alone can't confirm (numbers, names, dates).

Returns None when GEMINI_API_KEY is unset, recording unavailable, or on any
unrecoverable error — the caller falls back to text-only LiteLLM eval.
"""

from __future__ import annotations

import io
import json
import logging
from uuid import UUID

from src.config import get_settings
from src.llm.prompt_manager import compile_prompt
from src.llm.prompts.voice_screen_eval_audio import VOICE_SCREEN_EVAL_AUDIO_V1
from src.models.v1 import VoiceCallScore
from src.services.scoring_context import compute_spec_weighted_score, scoring_prompt_vars

logger = logging.getLogger(__name__)

_GEMINI_MODEL = "gemini-2.5-flash"


async def evaluate_voice_call_with_audio(
    *,
    recording_r2_key: str,
    answers: list[dict],
    role_title: str,
    jd_text: str | None = None,
    ctc_min_lpa: float | None = None,
    ctc_max_lpa: float | None = None,
    max_notice_days: int | None = None,
    role_location: str | None = None,
    remote_policy: str | None = None,
    application_id: UUID | None = None,
    evaluation_spec: dict | None = None,
    company_context: dict | None = None,
) -> VoiceCallScore | None:
    """Evaluate the voice call using Gemini 2.5 Flash with audio + transcript.

    Returns a fully populated VoiceCallScore or None on failure.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        logger.debug("GEMINI_API_KEY not set — skipping Gemini eval")
        return None

    try:
        from google import genai  # type: ignore[import-untyped]
        from google.genai import types as gtypes  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("google-genai not installed — skipping Gemini eval")
        return None

    # Download audio from R2
    try:
        from src.services.file_storage import download
        bucket = settings.r2_bucket_resumes
        audio_bytes = await download(bucket, recording_r2_key)
    except Exception as exc:
        logger.warning("audio download failed for %s: %s", recording_r2_key, exc)
        return None

    if not audio_bytes:
        logger.warning("empty audio bytes for %s", recording_r2_key)
        return None

    client = genai.Client(api_key=settings.gemini_api_key)
    gemini_file = None

    try:
        # Detect MIME type from key extension; default to mp3
        key_lower = recording_r2_key.lower()
        if key_lower.endswith(".wav"):
            mime = "audio/wav"
        elif key_lower.endswith(".ogg"):
            mime = "audio/ogg"
        elif key_lower.endswith(".m4a"):
            mime = "audio/mp4"
        elif key_lower.endswith(".webm"):
            mime = "audio/webm"
        else:
            mime = "audio/mpeg"

        # Upload to Gemini Files API
        gemini_file = await client.aio.files.upload(
            file=io.BytesIO(audio_bytes),
            config=gtypes.UploadFileConfig(mime_type=mime),
        )
        logger.info("uploaded audio to Gemini Files: %s (app=%s)", gemini_file.name, application_id)

        # Build prompt with transcript + role context
        answers_json = json.dumps(answers, ensure_ascii=False)[:8000]
        prompt_text = compile_prompt(
            "voice_screen_eval",
            fallback=VOICE_SCREEN_EVAL_AUDIO_V1,
            role_title=role_title,
            jd_text=(jd_text or "")[:3000],
            ctc_min_lpa=ctc_min_lpa if ctc_min_lpa is not None else "n/a",
            ctc_max_lpa=ctc_max_lpa if ctc_max_lpa is not None else "n/a",
            max_notice_days=max_notice_days if max_notice_days is not None else "n/a",
            role_location=role_location or "n/a",
            remote_policy=remote_policy or "n/a",
            answers_json=answers_json,
            **scoring_prompt_vars(evaluation_spec, company_context),
        )

        response = await client.aio.models.generate_content(
            model=_GEMINI_MODEL,
            contents=[
                gtypes.Part.from_uri(file_uri=gemini_file.uri, mime_type=mime),
                prompt_text,
            ],
        )

        raw_text = response.text.strip()
        # Strip markdown fences if model wrapped the JSON
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            raw_text = "\n".join(ln for ln in lines if not ln.startswith("```")).strip()

        data = json.loads(raw_text)
        score = VoiceCallScore.model_validate(data)
        # Recompute overall from criteria_scores when the LLM scored spec dimensions.
        if score.criteria_scores:
            spec_score = compute_spec_weighted_score(score.criteria_scores)
            if spec_score is not None:
                score.overall_score = spec_score
        logger.info(
            "Gemini eval done: app=%s score=%d (verdict owned by route_score)",
            application_id, score.overall_score,
        )
        return score

    except Exception as exc:
        logger.warning(
            "Gemini voice eval failed for app=%s key=%s: %s",
            application_id, recording_r2_key, exc,
        )
        return None

    finally:
        # Best-effort cleanup of the uploaded Gemini file
        if gemini_file is not None:
            try:
                await client.aio.files.delete(name=gemini_file.name)
            except Exception as del_exc:
                logger.debug("Gemini file cleanup failed: %s", del_exc)
