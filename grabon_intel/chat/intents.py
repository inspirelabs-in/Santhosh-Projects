"""Intent classification.

Six intents cover the bulk of rep traffic. The classifier is one Haiku
call returning strict JSON. Below ~95% confidence we fall to `query` and
ask the LLM to answer free-form against DB context (cheap, never wrong).

Intents:
  - discover : run a collector ("find brands matching X")
  - dossier  : kick a DossierWF for a specific brand
  - query    : read-only DB lookup or general Q&A grounded in stored data
  - draft    : produce/edit outreach (uses opportunity from latest dossier)
  - rescore  : rerun scoring on an existing brand (cheaper than full dossier)
  - help     : meta — list capabilities
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..llm import Tier, complete
from ..logging import get_logger

log = get_logger(__name__)


class Intent(str, Enum):
    DISCOVER = "discover"
    DOSSIER = "dossier"
    QUERY = "query"
    DRAFT = "draft"
    RESCORE = "rescore"
    HELP = "help"


@dataclass(slots=True)
class IntentClassification:
    intent: Intent
    confidence: float
    args: dict[str, Any]
    explanation: str


_SYSTEM = """You classify revenue-team requests into one intent + arguments.
Intents and their args:
  discover  : {collector: 'google_ads_transparency'|'meta_ad_library'|'linkedin_jobs'|'news_polling', params: object}
  dossier   : {brand_id?: int, brand_name?: str, brand_domain?: str, reason?: str}
  query     : {natural_language: str, scope?: 'brands'|'signals'|'dossiers'|'pipeline'}
  draft     : {brand_id?: int, brand_name?: str, channel?: 'email'|'linkedin'|'voicemail', tone?: str}
  rescore   : {brand_id?: int, brand_name?: str}
  help      : {}

Rules:
- If a single brand name or URL is present, prefer dossier/draft/rescore over discover.
- Choose discover ONLY for pluralised asks ("find brands matching", "20 D2C beauty").
- Default collector for discover is 'google_ads_transparency'. Use 'meta_ad_library' only if user explicitly mentions Meta/Facebook ads.
- params must include search_terms (the user's query distilled to key terms).
- If unsure, choose `query` (free-form answer, safer than wrong action).
- confidence is 0-1 reflecting how sure you are.
- Use conversation history (if provided) to resolve references like "them", "that brand", "it", "their competitors". Carry forward brand names/domains from prior turns.

Output strict JSON: {intent, confidence, args, explanation}."""


async def classify(
    user_text: str,
    history: list[dict[str, str]] | None = None,
) -> IntentClassification:
    prompt_parts: list[str] = []
    if history:
        for turn in history[-6:]:
            role = turn.get("role", "user")
            prompt_parts.append(f"{role.capitalize()}: {turn.get('content', '')}")
    prompt_parts.append(f"User: {user_text}")
    prompt_parts.append("\nReturn JSON only.")

    res = await complete(
        tier=Tier.FAST,
        system=_SYSTEM,
        prompt="\n".join(prompt_parts),
        json_mode=True,
        max_output_tokens=250,
        temperature=0.0,
    )
    raw = res.content
    try:
        data = json.loads(raw)
    except Exception:
        log.warning("intent.parse_failed", raw=raw[:300])
        return IntentClassification(
            intent=Intent.QUERY,
            confidence=0.0,
            args={"natural_language": user_text},
            explanation="parse_fallback",
        )
    intent_raw = (data.get("intent") or "query").lower()
    try:
        intent = Intent(intent_raw)
    except ValueError:
        intent = Intent.QUERY
    conf = float(data.get("confidence") or 0.0)
    # Confidence floor: low confidence collapses to safe `query`.
    if conf < 0.55 and intent in {Intent.DISCOVER, Intent.DOSSIER, Intent.RESCORE}:
        return IntentClassification(
            intent=Intent.QUERY,
            confidence=conf,
            args={"natural_language": user_text},
            explanation=f"low_confidence({conf:.2f}); collapsed from {intent.value}",
        )
    return IntentClassification(
        intent=intent,
        confidence=conf,
        args=dict(data.get("args") or {}),
        explanation=str(data.get("explanation") or ""),
    )
