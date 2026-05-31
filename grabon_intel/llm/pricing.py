"""Per-1M-token USD prices. Used for cost estimate before/after a call.

Numbers are conservative published list prices as of 2026-05. NVIDIA NIM
free tier is treated as $0 input + $0 output. Cents = USD * 100. Cost
estimate uses input/output tokens reported by the provider.

Override via env `GRABON_LLM_PRICE_<MODEL_SLUG>_IN` / `_OUT` in USD per 1M.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Price:
    input_per_million_usd: float
    output_per_million_usd: float
    cache_read_per_million_usd: float | None = None


# model id → price. Keys match LiteLLM-style ids: "<provider>/<model>".
_DEFAULTS: dict[str, Price] = {
    "anthropic/claude-sonnet-4-6": Price(3.00, 15.00, 0.30),
    "anthropic/claude-haiku-4-5": Price(1.00, 5.00, 0.10),
    "anthropic/claude-opus-4-7": Price(15.00, 75.00, 1.50),
    "openai/gpt-4.1-nano": Price(0.10, 0.40),
    "openai/gpt-4.1-mini": Price(0.40, 1.60),
    "openai/gpt-4o-mini": Price(0.15, 0.60),
    "openai/gpt-4o": Price(2.50, 10.00),
    "nvidia/llama-3.3-70b": Price(0.0, 0.0),  # free tier
    "nvidia/llama-3.1-70b": Price(0.0, 0.0),
    "nvidia/meta/llama-3.3-70b-instruct": Price(0.0, 0.0),
    "nvidia/meta/llama-3.1-70b-instruct": Price(0.0, 0.0),
}


def _slug(model_id: str) -> str:
    return model_id.replace("/", "_").replace("-", "_").upper()


def price_for(model_id: str) -> Price:
    base = _DEFAULTS.get(model_id)
    if base is None:
        # Try prefix match for nvidia/* models (e.g. nvidia/meta/llama → nvidia)
        if model_id.startswith("nvidia/"):
            base = Price(0.0, 0.0)
        else:
            base = Price(1.0, 3.0)  # safe pessimistic default
    slug = _slug(model_id)
    in_p = float(os.getenv(f"GRABON_LLM_PRICE_{slug}_IN", base.input_per_million_usd))
    out_p = float(os.getenv(f"GRABON_LLM_PRICE_{slug}_OUT", base.output_per_million_usd))
    return Price(in_p, out_p, base.cache_read_per_million_usd)


def cost_cents(model_id: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> int:
    p = price_for(model_id)
    usd = (
        (input_tokens - cache_read_tokens) / 1_000_000 * p.input_per_million_usd
        + cache_read_tokens / 1_000_000 * (p.cache_read_per_million_usd or p.input_per_million_usd)
        + output_tokens / 1_000_000 * p.output_per_million_usd
    )
    # Round up to next cent so we never under-bill the cap.
    return max(0, int(usd * 100 + 0.999))
