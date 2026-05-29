"""LLM rate limiting + spend caps.

Four independent guards — any one firing blocks the call:

  1. Per-minute token bucket (burst protection)           — Redis `INCR` with 60s expiry
  2. Per-day total call count                              — Redis `INCR` with 24h expiry
  3. Per-day total token count (approx, uses output ceil)  — Redis `INCRBY` with 24h expiry
  4. Per-candidate monthly call count                      — Redis `INCR` with 30d expiry

A fifth guard -- the USD budget -- is advisory: we compute the estimated
daily spend from token counters and log a warning at 80% and refuse calls
above 2x the configured limit.

All counters are Redis-backed so the limit applies across every worker and
API process. A Redis outage fails-open (logs a warning, lets the call
through) rather than hard-failing every LLM call -- the guards are a safety
net, not the primary path.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from uuid import UUID

from src.config import get_settings
from src.db.connection import get_redis

logger = logging.getLogger(__name__)

# Rough per-1k-token prices (USD) — adjust as OpenAI prices change.
# Over-estimating is fine: budget guard triggers on the high side, which is
# what you want.
_MODEL_COST_PER_1K = {
    "openai/gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "openai/gpt-4o": {"input": 0.0025, "output": 0.01},
    "openai/gpt-4.1-mini": {"input": 0.00015, "output": 0.0006},
    "openai/gpt-4.1": {"input": 0.002, "output": 0.008},
    "anthropic/claude-haiku-4-5-20251001": {"input": 0.001, "output": 0.005},
    "anthropic/claude-sonnet-4-7": {"input": 0.003, "output": 0.015},
    # Groq pricing (per 1k tokens, USD) — current free-tier still incurs this
    # billed amount only past the daily allowance. Used only for budget
    # tracking; free-tier usage will read as small numbers.
    "groq/llama-3.1-8b-instant": {"input": 0.00005, "output": 0.00008},
    "groq/llama-3.3-70b-versatile": {"input": 0.00059, "output": 0.00079},
    "groq/openai/gpt-oss-120b": {"input": 0.00015, "output": 0.0006},
    "groq/mixtral-8x7b-32768": {"input": 0.00024, "output": 0.00024},
    # Ollama is local — zero cost.
}


class RateLimitExceeded(RuntimeError):
    """Raised when any of the caps is hit. Non-retryable."""


@dataclass
class UsageSnapshot:
    minute_calls: int
    day_calls: int
    day_tokens: int
    candidate_monthly_calls: int | None
    day_usd_spent: float


def _now_minute() -> int:
    return int(time.time() // 60)


def _day_key(suffix: str) -> str:
    day = time.strftime("%Y%m%d", time.gmtime())
    return f"ratelimit:{suffix}:{day}"


def _minute_key() -> str:
    return f"ratelimit:rpm:{_now_minute()}"


def _candidate_month_key(candidate_id: UUID) -> str:
    month = time.strftime("%Y%m", time.gmtime())
    return f"ratelimit:cand:{candidate_id}:{month}"


async def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = _MODEL_COST_PER_1K.get(model)
    if prices is None:
        return 0.0
    return (input_tokens / 1000.0) * prices["input"] + (output_tokens / 1000.0) * prices["output"]


async def check_before_call(
    *,
    candidate_id: UUID | None,
    model: str,
    est_output_tokens: int = 1500,
) -> None:
    """Raise `RateLimitExceeded` if any cap is exceeded.

    Call this *before* the HTTP request to the LLM. `record_after_call`
    updates the token counters with actual usage once the response returns.
    """
    settings = get_settings()
    redis = get_redis()

    # --- Minute burst (token bucket) ------------------------------------
    try:
        cur = await redis.incr(_minute_key())
        if cur == 1:
            await redis.expire(_minute_key(), 90)
        if cur > settings.llm_rate_limit_per_minute:
            raise RateLimitExceeded(
                f"per-minute LLM cap exceeded ({cur}/{settings.llm_rate_limit_per_minute})"
            )
    except RateLimitExceeded:
        raise
    except Exception as e:  # noqa: BLE001 -- fail-open on Redis outage
        logger.warning("Rate-limit Redis check failed, failing open: %s", e)
        return  # don't bother with the other checks if Redis is down

    # --- Daily call count ----------------------------------------------
    day_call_key = _day_key("calls")
    cur_day = await redis.incr(day_call_key)
    if cur_day == 1:
        await redis.expire(day_call_key, 26 * 3600)
    if cur_day > settings.llm_daily_call_limit:
        raise RateLimitExceeded(
            f"daily LLM call cap exceeded ({cur_day}/{settings.llm_daily_call_limit})"
        )

    # --- Daily token estimate (pre-flight) -----------------------------
    # This uses the caller's est_output_tokens -- we don't know the real
    # input tokens until LiteLLM returns, so this guard triggers only on
    # pathological looping. Hard-stop numbers are recorded after the call.
    day_tok_key = _day_key("tokens")
    current_tokens = int(await redis.get(day_tok_key) or 0)
    if current_tokens > settings.llm_daily_token_limit:
        raise RateLimitExceeded(
            f"daily LLM token cap exceeded ({current_tokens}/{settings.llm_daily_token_limit})"
        )

    # --- Per-candidate monthly cap --------------------------------------
    if candidate_id is not None:
        ck = _candidate_month_key(candidate_id)
        cur_c = await redis.incr(ck)
        if cur_c == 1:
            await redis.expire(ck, 31 * 24 * 3600)
        if cur_c > settings.llm_per_candidate_monthly_limit:
            raise RateLimitExceeded(
                f"per-candidate monthly cap exceeded ({cur_c}/{settings.llm_per_candidate_monthly_limit})"
            )

    # --- Daily USD budget advisory --------------------------------------
    day_usd_key = _day_key("usd_cents")
    usd_cents = int(await redis.get(day_usd_key) or 0)
    spent = usd_cents / 100.0
    budget = settings.llm_daily_budget_usd
    if spent >= budget * 2.0:
        raise RateLimitExceeded(
            f"daily USD budget hard cap reached (${spent:.2f} > 2x ${budget})"
        )
    if spent >= budget * 0.8 and spent < budget * 0.8 + 0.02:
        logger.warning("LLM daily spend at %.2f USD (80%% of $%s budget)", spent, budget)


async def record_after_call(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Update token + USD counters after a successful LLM response."""
    redis = get_redis()
    total = input_tokens + output_tokens
    if total <= 0:
        return

    day_tok_key = _day_key("tokens")
    try:
        await redis.incrby(day_tok_key, total)
        await redis.expire(day_tok_key, 26 * 3600)

        cost = await estimate_cost_usd(model, input_tokens, output_tokens)
        if cost > 0:
            day_usd_key = _day_key("usd_cents")
            await redis.incrby(day_usd_key, int(round(cost * 100)))
            await redis.expire(day_usd_key, 26 * 3600)
    except Exception as e:  # noqa: BLE001
        logger.warning("Rate-limit post-call record failed: %s", e)


async def snapshot(candidate_id: UUID | None = None) -> UsageSnapshot:
    """For the dashboard Settings page."""
    redis = get_redis()
    try:
        return UsageSnapshot(
            minute_calls=int(await redis.get(_minute_key()) or 0),
            day_calls=int(await redis.get(_day_key("calls")) or 0),
            day_tokens=int(await redis.get(_day_key("tokens")) or 0),
            candidate_monthly_calls=(
                int(await redis.get(_candidate_month_key(candidate_id)) or 0)
                if candidate_id is not None
                else None
            ),
            day_usd_spent=int(await redis.get(_day_key("usd_cents")) or 0) / 100.0,
        )
    except Exception:
        return UsageSnapshot(0, 0, 0, None, 0.0)
