"""Org-onboarding research sub-agent (Gemini + Google Search grounding).

Hands a research instruction to Gemini with native Google Search grounding +
url-context, and returns a small structured draft mapped to the ``HiringPersona``
schema plus its sources / gaps. The heavy page-reading happens inside this call
(Gemini's context), so the Pulse agent that invokes it stays lean.

Never raises: on missing key, timeout, API error, or unparseable output it
returns a best-effort dict with an ``error``/``notes`` field so Pulse can still
respond conversationally.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src.config import get_settings
from src.constants.gemini_models import GEMINI_MODELS
from src.constants.research import (
    RESEARCH_ENABLE_GROUNDING,
    RESEARCH_MAX_OUTPUT_TOKENS,
    RESEARCH_TIMEOUT_SECONDS,
)
from src.llm.prompts.org_research import ORG_RESEARCH_V1

logger = logging.getLogger(__name__)

_MODEL = GEMINI_MODELS["org_onboarding"]

# Persona fields the research draft may legitimately carry (mirrors the prompt).
_DRAFT_FIELDS = (
    "company_name",
    "mission",
    "domain_context",
    "values",
    "what_good_looks_like",
    "hiring_philosophy",
    "tone",
)


def _empty(notes: str, error: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "draft": {},
        "sources": [],
        "gaps": list(_DRAFT_FIELDS),
        "notes": notes,
        "confidence": "low",
    }
    if error:
        out["error"] = error
    return out


def _grounding_tools(gtypes: Any) -> list[Any]:
    """Build the Google Search + url-context tools, tolerating SDK differences."""
    tools: list[Any] = []
    try:
        tools.append(gtypes.Tool(google_search=gtypes.GoogleSearch()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("google_search tool unavailable: %s", exc)
    try:
        tools.append(gtypes.Tool(url_context=gtypes.UrlContext()))
    except Exception as exc:  # noqa: BLE001
        logger.debug("url_context tool unavailable: %s", exc)
    return tools


def _extract_sources(response: Any) -> list[str]:
    """Pull grounded source URLs from the response's grounding metadata."""
    urls: list[str] = []
    try:
        for cand in response.candidates or []:
            meta = getattr(cand, "grounding_metadata", None)
            for chunk in getattr(meta, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                uri = getattr(web, "uri", None)
                if isinstance(uri, str) and uri and uri not in urls:
                    urls.append(uri)
    except Exception:  # noqa: BLE001
        pass
    return urls


def _parse_json_block(raw: str) -> dict[str, Any] | None:
    """Extract the JSON object from a possibly fenced model response."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(ln for ln in lines if not ln.startswith("```")).strip()
    # Fall back to the outermost braces if there's stray prose.
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        text = text[start : end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


async def research_org(
    *,
    instruction: str,
    org_name: str | None = None,
    org_url: str | None = None,
    focus_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Research an org and return ``{draft, sources, gaps, notes, confidence}``."""
    settings = get_settings()
    if not settings.gemini_api_key:
        return _empty("GEMINI_API_KEY not set — research unavailable.", error="no_api_key")

    try:
        from google import genai  # type: ignore[import-untyped]
        from google.genai import types as gtypes  # type: ignore[import-untyped]
    except ImportError:
        return _empty("google-genai not installed — research unavailable.", error="sdk_missing")

    prompt = ORG_RESEARCH_V1.format(
        org_name=(org_name or "(unknown — infer from instruction/URL)"),
        org_url=(org_url or "(none provided)"),
        focus_fields=(", ".join(focus_fields) if focus_fields else "any"),
        instruction=(instruction or "").strip(),
    )

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        config_kwargs: dict[str, Any] = {"max_output_tokens": RESEARCH_MAX_OUTPUT_TOKENS}
        if RESEARCH_ENABLE_GROUNDING:
            tools = _grounding_tools(gtypes)
            if tools:
                config_kwargs["tools"] = tools
        config = gtypes.GenerateContentConfig(**config_kwargs)

        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=_MODEL,
                contents=[prompt],
                config=config,
            ),
            timeout=RESEARCH_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return _empty("Research timed out.", error="timeout")
    except Exception as exc:  # noqa: BLE001
        logger.warning("org research failed (model=%s): %s", _MODEL, exc)
        return _empty(f"Research call failed: {exc}", error="api_error")

    grounded_sources = _extract_sources(response)
    parsed = _parse_json_block(getattr(response, "text", "") or "")
    if parsed is None:
        # Model returned prose we couldn't parse — hand it back as notes so the
        # recruiter still gets value; sources may still be present.
        raw = (getattr(response, "text", "") or "").strip()
        out = _empty(raw[:1500] or "No parseable output.", error="unparseable")
        out["sources"] = grounded_sources
        return out

    # Keep only known draft fields; drop empties.
    raw_draft = parsed.get("draft") if isinstance(parsed.get("draft"), dict) else {}
    draft: dict[str, Any] = {}
    for f in _DRAFT_FIELDS:
        v = raw_draft.get(f)
        if isinstance(v, str) and v.strip():
            draft[f] = v.strip()
        elif isinstance(v, list) and v:
            draft[f] = v

    sources = parsed.get("sources") if isinstance(parsed.get("sources"), list) else []
    sources = [s for s in sources if isinstance(s, str) and s.strip()]
    for u in grounded_sources:
        if u not in sources:
            sources.append(u)

    gaps = parsed.get("gaps") if isinstance(parsed.get("gaps"), list) else []
    gaps = [g for g in gaps if isinstance(g, str)]
    # Anything not drafted is a gap, even if the model forgot to list it.
    for f in _DRAFT_FIELDS:
        if f not in draft and f not in gaps:
            gaps.append(f)

    confidence = parsed.get("confidence")
    if confidence not in ("low", "medium", "high"):
        confidence = "medium" if draft else "low"

    return {
        "draft": draft,
        "sources": sources,
        "gaps": gaps,
        "notes": str(parsed.get("notes") or "").strip(),
        "confidence": confidence,
    }
