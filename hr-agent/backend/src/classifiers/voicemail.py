"""LLM-based voicemail classifier with regex fallback.

Replaces the old 10-pattern substring match with a structured LLM call.
If LLM fails (timeout, parse error, feature flag off), falls back to
the original pattern matching — zero behavior change on failure.
"""

from __future__ import annotations
from src.llm.model_registry import Stage, model_for

import logging
from uuid import UUID

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Legacy patterns (fallback)
# ---------------------------------------------------------------------------

_VOICEMAIL_PATTERNS = (
    "leave a message",
    "leave your message",
    "after the beep",
    "after the tone",
    "voicemail",
    "voice mail",
    "currently unavailable",
    "not available right now",
    "please record",
    "you have reached",
)


def _detect_voicemail_regex(answers: list[dict] | None) -> bool:
    """Original substring-match voicemail detection."""
    if not answers:
        return False
    blob = " ".join(
        str(a.get("answer_transcript") or a.get("answer") or "").lower()
        for a in answers
        if isinstance(a, dict)
    )
    if not blob.strip():
        return False
    return any(p in blob for p in _VOICEMAIL_PATTERNS)


# ---------------------------------------------------------------------------
# LLM classifier
# ---------------------------------------------------------------------------

class VoicemailClassification(BaseModel):
    is_voicemail: bool = Field(
        description="True if the transcript is a voicemail greeting, not a real conversation",
    )
    confidence: float = Field(
        ge=0, le=1,
        description="Confidence in the classification (0-1)",
    )
    reasoning: str = Field(
        description="Brief explanation of why this is or isn't a voicemail",
    )


_SYSTEM_PROMPT = """You classify voice call transcripts as voicemail or real conversation.

A voicemail transcript typically contains:
- Automated greeting phrases ("you have reached", "leave a message after the beep")
- No real back-and-forth dialogue
- Generic carrier/phone system language
- Very short or repetitive content from the candidate side

A real conversation has:
- Actual responses to interview questions
- Back-and-forth dialogue
- Substantive content even if brief or poor quality

Classify ONLY based on whether this is a voicemail recording vs a real person talking.
Do NOT judge answer quality — a bad interview is still a real conversation."""


async def classify_voicemail(
    answers: list[dict] | None,
    *,
    application_id: UUID | None = None,
    voice_call_id: UUID | None = None,
) -> tuple[bool, float, str]:
    """Classify whether a voice call transcript is voicemail.

    Returns (is_voicemail, confidence, reasoning).
    Falls back to regex if LLM unavailable.
    """
    if not answers:
        return False, 1.0, "no answers provided"

    blob = " ".join(
        str(a.get("answer_transcript") or a.get("answer") or "").lower()
        for a in answers
        if isinstance(a, dict)
    )
    if not blob.strip():
        return False, 1.0, "empty transcript"

    from src.config import get_settings
    settings = get_settings()

    if not getattr(settings, "enable_llm_classifiers", False):
        result = _detect_voicemail_regex(answers)
        return result, 0.8 if result else 0.9, "regex fallback (LLM classifiers disabled)"

    try:
        from src.llm.client import get_llm_client
        client = get_llm_client()

        transcript_text = "\n".join(
            f"Q: {a.get('question', '?')}\nA: {a.get('answer_transcript') or a.get('answer') or ''}"
            for a in answers
            if isinstance(a, dict)
        )

        result = await client.complete(
            prompt=f"Classify this voice call transcript:\n\n{transcript_text[:3000]}",
            response_model=VoicemailClassification,
            model=model_for(Stage.CLASSIFY_VOICEMAIL),
            trace_name="classify_voicemail",
            prompt_version="voicemail_v1",
            application_id=application_id,
            temperature=0.0,
            max_tokens=256,
        )

        return result.parsed.is_voicemail, result.parsed.confidence, result.parsed.reasoning

    except Exception:
        logger.warning("voicemail LLM classifier failed, falling back to regex", exc_info=True)
        result = _detect_voicemail_regex(answers)
        return result, 0.7 if result else 0.8, "regex fallback (LLM error)"
