"""Candidate intent classifier for inbound messages.

Classifies messages arriving outside the normal chat flow — email replies,
off-schedule messages, inbound calls. Powers supervisor routing decisions.

Intent categories:
- screening_response: answering screening questions
- reschedule_request: wants to change a meeting/deadline
- withdrawal: withdrawing application
- question: asking about role/process/status
- document_submission: sending resume, portfolio, assignment
- off_topic: spam, unrelated, social
"""

from __future__ import annotations

import logging
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CandidateIntent(StrEnum):
    SCREENING_RESPONSE = "screening_response"
    RESCHEDULE_REQUEST = "reschedule_request"
    WITHDRAWAL = "withdrawal"
    QUESTION = "question"
    DOCUMENT_SUBMISSION = "document_submission"
    OFF_TOPIC = "off_topic"


class IntentClassification(BaseModel):
    intent: CandidateIntent = Field(
        description="Primary intent of the candidate message",
    )
    confidence: float = Field(
        ge=0, le=1,
        description="Classification confidence (0-1)",
    )
    reasoning: str = Field(
        description="Brief explanation of classification decision",
    )
    urgency: str = Field(
        default="normal",
        description="low | normal | high — high if withdrawal or time-sensitive reschedule",
    )
    extracted_details: dict = Field(
        default_factory=dict,
        description="Relevant extracted info: requested_date, mentioned_stage, etc.",
    )


# ---------------------------------------------------------------------------
# Keyword fallback
# ---------------------------------------------------------------------------

_WITHDRAWAL_SIGNALS = frozenset({
    "withdraw", "withdrawing", "pull out", "no longer interested",
    "not interested", "decline", "opt out", "cancel my application",
    "remove my application", "don't want to proceed",
})

_RESCHEDULE_SIGNALS = frozenset({
    "reschedule", "postpone", "move the meeting", "change the date",
    "can't make it", "cannot attend", "different time", "push back",
    "delay", "new date", "another slot",
})

_DOCUMENT_SIGNALS = frozenset({
    "attached", "please find", "resume", "portfolio", "assignment",
    "submission", "completed task", "here is my", "pfa",
})


def _classify_intent_keywords(text: str) -> IntentClassification:
    """Keyword-based fallback classifier."""
    lower = text.lower()

    for signal in _WITHDRAWAL_SIGNALS:
        if signal in lower:
            return IntentClassification(
                intent=CandidateIntent.WITHDRAWAL,
                confidence=0.7,
                reasoning=f"keyword match: '{signal}'",
                urgency="high",
            )

    for signal in _RESCHEDULE_SIGNALS:
        if signal in lower:
            return IntentClassification(
                intent=CandidateIntent.RESCHEDULE_REQUEST,
                confidence=0.7,
                reasoning=f"keyword match: '{signal}'",
                urgency="high",
            )

    for signal in _DOCUMENT_SIGNALS:
        if signal in lower:
            return IntentClassification(
                intent=CandidateIntent.DOCUMENT_SUBMISSION,
                confidence=0.6,
                reasoning=f"keyword match: '{signal}'",
                urgency="normal",
            )

    return IntentClassification(
        intent=CandidateIntent.QUESTION,
        confidence=0.4,
        reasoning="no strong keyword signal, defaulting to question",
        urgency="normal",
    )


# ---------------------------------------------------------------------------
# LLM classifier
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You classify inbound candidate messages in a recruitment pipeline.

The candidate may be at any stage: applied, screening, assignment, interview, offer.
Classify the PRIMARY intent of their message.

Intent categories:
- screening_response: answering screening/interview questions
- reschedule_request: wants to change a scheduled meeting, deadline, or call time
- withdrawal: wants to withdraw their application or stop the process
- question: asking about the role, company, process, timeline, or status
- document_submission: sending or referencing a resume, portfolio, assignment, or other document
- off_topic: social chat, spam, unrelated content, auto-replies

Also assess urgency:
- high: withdrawal, time-sensitive reschedule (within 24h), or urgent blocker
- normal: standard communication
- low: FYI, social, non-actionable

Extract relevant details into extracted_details:
- For reschedule: requested_date, original_date, reason
- For withdrawal: stated_reason
- For question: topic
- For document: document_type"""


async def classify_candidate_intent(
    message_text: str,
    *,
    current_stage: str | None = None,
    has_attachments: bool = False,
    application_id: UUID | None = None,
    candidate_id: UUID | None = None,
) -> IntentClassification:
    """Classify candidate message intent.

    Falls back to keyword matching if LLM unavailable.
    """
    if not message_text or not message_text.strip():
        return IntentClassification(
            intent=CandidateIntent.OFF_TOPIC,
            confidence=1.0,
            reasoning="empty message",
            urgency="low",
        )

    if has_attachments and len(message_text.strip()) < 50:
        return IntentClassification(
            intent=CandidateIntent.DOCUMENT_SUBMISSION,
            confidence=0.85,
            reasoning="short message with attachments",
            urgency="normal",
        )

    from src.config import get_settings
    settings = get_settings()

    if not settings.enable_llm_classifiers:
        return _classify_intent_keywords(message_text)

    try:
        from src.llm.client import get_llm_client
        client = get_llm_client()

        context_line = f"\nCandidate is currently at stage: {current_stage}" if current_stage else ""
        attachment_line = "\nMessage includes file attachments." if has_attachments else ""

        result = await client.complete(
            prompt=(
                f"Classify this candidate message:{context_line}{attachment_line}"
                f"\n\n---\n{message_text[:4000]}\n---"
            ),
            response_model=IntentClassification,
            model=settings.llm_model_fast,
            trace_name="classify_candidate_intent",
            prompt_version="intent_v1",
            application_id=application_id,
            candidate_id=candidate_id,
            temperature=0.0,
            max_tokens=512,
        )

        return result.parsed

    except Exception:
        logger.warning("intent LLM classifier failed, falling back to keywords", exc_info=True)
        return _classify_intent_keywords(message_text)
