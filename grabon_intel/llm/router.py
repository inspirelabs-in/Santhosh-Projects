"""LLM router on top of LiteLLM.

- Tiered model selection (cheap / fast / smart / fallback) configured via env.
- Per-call cost estimate using `pricing.cost_cents`.
- Atomic budget gate via `budget.try_debit` BEFORE the call (worst-case max
  tokens). Refund the difference after the real usage is known.
- Provider fallback on retryable errors.
- Returns content + raw usage + measured cost.

Why pre-debit + refund rather than post-debit:
  Two workers can each see `spent < cap` and both fire a $0.50 call, blowing
  past a $0.80 cap. Reserving the max cost up front and refunding is the
  cheapest race-free pattern that doesn't require a DB lock for the entire
  LLM call duration.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from enum import Enum
from collections.abc import AsyncIterator
from typing import Any

import logging as _logging
_logging.getLogger("LiteLLM").setLevel(_logging.ERROR)

import litellm
litellm.suppress_debug_info = True
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ..budget import refund, try_debit
from ..config import get_settings
from ..db import session as session_ctx
from ..logging import get_logger
from .cache import cache_get, cache_set
from .pricing import cost_cents

log = get_logger(__name__)

_ssl_configured = False


def _ensure_ssl_config() -> None:
    global _ssl_configured
    if _ssl_configured:
        return
    _ssl_configured = True
    s = get_settings()
    if not s.llm_ssl_verify:
        litellm.ssl_verify = False
        log.warning("llm.ssl_verify_disabled")


class Tier(str, Enum):
    CHEAP = "cheap"
    FAST = "fast"
    SMART = "smart"
    FALLBACK = "fallback"


class LLMError(RuntimeError):
    pass


class BudgetGateError(LLMError):
    pass


@dataclass(slots=True)
class LLMResult:
    content: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_cents: int
    raw: dict[str, Any]


# Retryable provider errors — keep broad, LiteLLM normalises across vendors.
_RETRYABLE = (
    litellm.RateLimitError,
    litellm.APIConnectionError,
    litellm.APIError,
    litellm.ServiceUnavailableError,
    litellm.Timeout,
    litellm.NotFoundError,
)


def _models_for(tier: Tier) -> list[str]:
    """Return list of models for a tier (comma-separated in config)."""
    s = get_settings()
    raw = {
        Tier.CHEAP: s.llm_tier_cheap,
        Tier.FAST: s.llm_tier_fast,
        Tier.SMART: s.llm_tier_smart,
        Tier.FALLBACK: s.llm_tier_fallback,
    }[tier]
    return [m.strip() for m in raw.split(",") if m.strip()]


def _model_for(tier: Tier) -> str:
    return _models_for(tier)[0]


def _provider_kwargs(model_id: str) -> dict[str, Any]:
    s = get_settings()
    if model_id.startswith("anthropic/"):
        return {"api_key": s.anthropic_api_key.get_secret_value()}
    if model_id.startswith("openai/"):
        return {"api_key": s.openai_api_key.get_secret_value()}
    if model_id.startswith("nvidia/"):
        # Strip full path (nvidia/meta/llama-3.3-70b-instruct → meta/llama-3.3-70b-instruct)
        bare_model = model_id.removeprefix("nvidia/")
        return {
            "api_base": s.nvidia_base_url,
            "api_key": s.nvidia_api_key.get_secret_value(),
            "custom_llm_provider": "openai",
            "model": bare_model,
        }
    return {}


def estimate_cost_cents(model_id: str, *, max_input_tokens: int, max_output_tokens: int) -> int:
    """Worst-case cost in cents — used for pre-debit budget reservation."""
    return cost_cents(model_id, max_input_tokens, max_output_tokens)


async def complete(
    *,
    tier: Tier = Tier.FAST,
    system: str | None = None,
    prompt: str,
    max_output_tokens: int = 800,
    temperature: float = 0.2,
    json_mode: bool = False,
    fallback_tiers: tuple[Tier, ...] = (Tier.FALLBACK,),
    skip_budget: bool = False,
) -> LLMResult:
    """Single-turn completion.

    Args:
        tier: primary tier; falls back to `fallback_tiers` on retryable errors.
        system: system prompt (None → omitted).
        prompt: user content.
        max_output_tokens: caps reply length AND budget reservation.
        json_mode: ask provider for JSON output where supported.
        skip_budget: bypass budget gate (dangerous; only for ops scripts).
    """
    _ensure_ssl_config()

    # --- Cache check (include json_mode in key to avoid cross-contamination) ---
    primary_model = _models_for(tier)[0]
    mode_tag = "[json]" if json_mode else "[text]"
    cache_prompt_key = mode_tag + (system or "") + "\n---\n" + prompt
    cached = await cache_get(primary_model, cache_prompt_key)
    if cached:
        log.info("llm.cache_hit", model=primary_model, saved_cents=cached["cost_cents"])
        return LLMResult(
            content=cached["content"],
            model_id=primary_model,
            input_tokens=cached["input_tokens"],
            output_tokens=cached["output_tokens"],
            cache_read_tokens=0,
            cost_cents=0,
            raw={"cached": True},
        )

    tiers = (tier, *fallback_tiers)
    last_exc: Exception | None = None

    for t in tiers:
        models = _models_for(t)
        for model_id in models:
            approx_input = (len(system or "") + len(prompt)) // 4 + 32
            max_cost = estimate_cost_cents(model_id, max_input_tokens=approx_input, max_output_tokens=max_output_tokens)

            if not skip_budget and max_cost > 0:
                try:
                    async with session_ctx() as ses:
                        reserved = await try_debit(ses, max_cost)
                    if reserved is None:
                        raise BudgetGateError(
                            f"budget cap would be exceeded by ~{max_cost}c reservation for {model_id}"
                        )
                except BudgetGateError:
                    raise
                except Exception as db_exc:
                    log.warning("llm.budget_gate_skipped", reason=str(db_exc))
                    skip_budget = True

            try:
                result = await _call(model_id, system, prompt, max_output_tokens, temperature, json_mode)
                delta = max_cost - result.cost_cents
                if not skip_budget and delta > 0:
                    try:
                        async with session_ctx() as ses:
                            await refund(ses, delta)
                    except Exception:
                        pass
                await cache_set(
                    model_id=model_id,
                    prompt=cache_prompt_key,
                    response=result.content,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cost_cents=result.cost_cents,
                )
                return result
            except _RETRYABLE as exc:
                if not skip_budget and max_cost > 0:
                    try:
                        async with session_ctx() as ses:
                            await refund(ses, max_cost)
                    except Exception:
                        pass
                last_exc = exc
                log.warning("llm.model_failed", tier=t.value, model=model_id, exc=str(exc))
                from ..logging import classify_error, record_error
                cat = classify_error(exc)
                record_error(
                    category=cat,
                    source=f"llm.{t.value}",
                    message=f"LLM {model_id} failed: {exc}"[:300],
                )
                await asyncio.sleep(random.uniform(0.5, 2.0))
                continue
            except Exception as exc:
                if not skip_budget and max_cost > 0:
                    try:
                        async with session_ctx() as ses:
                            await refund(ses, max_cost)
                    except Exception:
                        pass
                from ..logging import ErrorCategory, record_error
                record_error(
                    category=ErrorCategory.INTERNAL,
                    source=f"llm.{t.value}",
                    message=f"LLM call failed on {model_id}: {exc}"[:300],
                )
                raise LLMError(f"LLM call failed on {model_id}: {exc}") from exc

    from ..logging import ErrorCategory, record_error
    record_error(
        category=ErrorCategory.API_ERROR,
        source="llm",
        message=f"All LLM tiers exhausted: {last_exc}"[:300],
    )
    raise LLMError(f"all tiers exhausted: {last_exc}") from last_exc


async def _call(
    model_id: str,
    system: str | None,
    prompt: str,
    max_output_tokens: int,
    temperature: float,
    json_mode: bool,
) -> LLMResult:
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_output_tokens,
        "temperature": temperature,
        **_provider_kwargs(model_id),
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(2),
        wait=wait_exponential_jitter(initial=2, max=30),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    ):
        with attempt:
            resp = await litellm.acompletion(**kwargs)

    # LiteLLM normalises to OpenAI-style response.
    content = resp["choices"][0]["message"].get("content") or ""
    usage = resp.get("usage", {}) or {}
    in_tok = int(usage.get("prompt_tokens", 0))
    out_tok = int(usage.get("completion_tokens", 0))
    cache_read = int(usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)) if isinstance(usage, dict) else 0
    cents = cost_cents(model_id, in_tok, out_tok, cache_read)
    raw_dump = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)

    return LLMResult(
        content=content,
        model_id=model_id,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=cache_read,
        cost_cents=cents,
        raw=raw_dump,
    )


async def stream_complete(
    *,
    tier: Tier = Tier.FAST,
    system: str | None = None,
    messages: list[dict[str, str]] | None = None,
    prompt: str | None = None,
    max_output_tokens: int = 800,
    temperature: float = 0.2,
    skip_budget: bool = False,
) -> AsyncIterator[str]:
    """Streaming completion — yields content chunks as they arrive.

    Pass either `messages` (multi-turn) or `prompt` (single-turn, auto-wrapped).
    Final usage/cost is tracked internally; caller just iterates strings.
    """
    _ensure_ssl_config()
    model_id = _model_for(tier)

    chat_messages: list[dict[str, Any]] = []
    if system:
        chat_messages.append({"role": "system", "content": system})
    if messages:
        chat_messages.extend(messages)
    elif prompt:
        chat_messages.append({"role": "user", "content": prompt})

    total_chars = sum(len(m.get("content", "")) for m in chat_messages)
    approx_input = total_chars // 4 + 32
    max_cost = estimate_cost_cents(model_id, max_input_tokens=approx_input, max_output_tokens=max_output_tokens)

    if not skip_budget and max_cost > 0:
        try:
            async with session_ctx() as ses:
                reserved = await try_debit(ses, max_cost)
            if reserved is None:
                raise BudgetGateError(f"budget cap exceeded for streaming {model_id}")
        except BudgetGateError:
            raise
        except Exception:
            skip_budget = True

    kwargs: dict[str, Any] = {
        "model": model_id,
        "messages": chat_messages,
        "max_tokens": max_output_tokens,
        "temperature": temperature,
        "stream": True,
        **_provider_kwargs(model_id),
    }

    try:
        resp = await litellm.acompletion(**kwargs)
        collected = ""
        async for chunk in resp:
            delta = chunk["choices"][0].get("delta", {})
            text = delta.get("content")
            if text:
                collected += text
                yield text
        # Refund over-reservation
        out_tok = len(collected) // 4 + 1
        real_cost = cost_cents(model_id, approx_input, out_tok)
        delta_cost = max_cost - real_cost
        if not skip_budget and delta_cost > 0:
            try:
                async with session_ctx() as ses:
                    await refund(ses, delta_cost)
            except Exception:
                pass
    except Exception as exc:
        if not skip_budget and max_cost > 0:
            try:
                async with session_ctx() as ses:
                    await refund(ses, max_cost)
            except Exception:
                pass
        raise LLMError(f"streaming failed on {model_id}: {exc}") from exc


# small helper for sync contexts (tests, scripts)
def complete_sync(**kw: Any) -> LLMResult:
    return asyncio.run(complete(**kw))
