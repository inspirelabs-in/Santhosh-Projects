"""Meta ad creative vision analysis.

Provider order:
  1. OpenAI gpt-4o vision  (production; you have the key)
  2. Ollama llava           (fully-local dev; OLLAMA_BASE_URL)
  3. Anthropic Sonnet 4.6  (only if ANTHROPIC_API_KEY set — opt-in)

Returns structured JSON: theme, hook, cta, design_quality (0-1),
novelty (low|med|high), strengths[], weaknesses[].
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx
import litellm

from ..config import get_settings
from ..llm.pricing import cost_cents
from ..logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class CreativeAnalysis:
    theme: str | None
    hook: str | None
    cta: str | None
    design_quality: float | None
    novelty: str | None
    cost_cents: int
    raw: dict[str, Any]


def _pick_model() -> tuple[str, dict[str, Any]] | None:
    """Return (model_id, litellm_kwargs) for the first available vision model."""
    s = get_settings()
    if s.openai_api_key.get_secret_value():
        return "openai/gpt-4o", {"api_key": s.openai_api_key.get_secret_value()}
    if s.ollama_base_url:
        # Ollama OpenAI-compatible — use its vision model.
        return f"ollama/{s.ollama_vision_model}", {
            "api_base": s.ollama_base_url,
            "api_key": s.ollama_api_key,
            "custom_llm_provider": "openai",
        }
    if s.anthropic_api_key.get_secret_value():
        return "anthropic/claude-sonnet-4-6", {"api_key": s.anthropic_api_key.get_secret_value()}
    return None


class MetaCreativeVision:
    name = "meta_creative_vision"

    @property
    def available(self) -> bool:
        return _pick_model() is not None

    async def analyse(self, *, image_url: str | None, body_text: str | None) -> CreativeAnalysis:
        picked = _pick_model()
        if picked is None:
            log.info("meta_creative_vision.unavailable")
            return CreativeAnalysis(None, None, None, None, None, 0, {"skipped": True})
        model, kwargs = picked

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "You are a senior performance-marketing creative director. "
                    "Analyse this ad creative. Return strict JSON: "
                    "{theme, hook, cta, design_quality(0-1), novelty(low|med|high), "
                    "strengths[], weaknesses[]}.\n\n"
                    f"Ad body text: {body_text or '(none)'}"
                ),
            }
        ]
        if image_url:
            data_url = await _fetch_data_url(image_url)
            if data_url:
                content.append({"type": "image_url", "image_url": {"url": data_url}})

        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": content}],
            max_tokens=600,
            **kwargs,
        )
        raw = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        usage = raw.get("usage", {}) or {}
        cents = cost_cents(
            model, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        )
        body = resp["choices"][0]["message"].get("content") or ""
        parsed = _try_parse(body)
        return CreativeAnalysis(
            theme=parsed.get("theme"),
            hook=parsed.get("hook"),
            cta=parsed.get("cta"),
            design_quality=parsed.get("design_quality"),
            novelty=parsed.get("novelty"),
            cost_cents=cents,
            raw=parsed,
        )


async def _fetch_data_url(image_url: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(image_url)
            r.raise_for_status()
        b = base64.b64encode(r.content).decode("ascii")
        mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
        return f"data:{mime};base64,{b}"
    except Exception as exc:  # noqa: BLE001
        log.warning("meta_creative_vision.fetch_failed", url=image_url, exc=str(exc))
        return None


def _try_parse(text: str) -> dict[str, Any]:
    import json

    try:
        return json.loads(text)
    except Exception:
        s = text.find("{")
        e = text.rfind("}")
        if 0 <= s < e:
            try:
                return json.loads(text[s : e + 1])
            except Exception:
                pass
        return {"_raw": text[:2000]}
