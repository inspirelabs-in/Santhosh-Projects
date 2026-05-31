"""LLM response cache — DB-backed deduplication for repeated prompts.

Caches LLM responses by (model_id, prompt_hash) to avoid paying for
identical queries. Particularly useful for scoring and research where
the same brand might be re-analyzed.

TTL-based: cache entries expire after configurable hours (default 24).
"""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

from sqlalchemy import text

from ..db import session as session_ctx
from ..logging import get_logger

log = get_logger(__name__)

DEFAULT_TTL_HOURS = 24


async def cache_get(model_id: str, prompt: str) -> dict[str, Any] | None:
    """Look up a cached response. Returns None on miss."""
    key = _cache_key(model_id, prompt)
    try:
        async with session_ctx() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT response, input_tokens, output_tokens, cost_cents "
                        "FROM llm_cache WHERE cache_key = :k "
                        "AND (expires_at IS NULL OR expires_at > NOW())"
                    ),
                    {"k": key},
                )
            ).first()
        if row:
            log.debug("llm_cache.hit", key=key[:16])
            return {
                "content": row[0],
                "input_tokens": row[1],
                "output_tokens": row[2],
                "cost_cents": row[3],
                "cached": True,
            }
    except Exception as exc:
        log.debug("llm_cache.get_error", error=str(exc))
    return None


async def cache_set(
    model_id: str,
    prompt: str,
    response: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_cents: int = 0,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> None:
    """Store a response in cache."""
    key = _cache_key(model_id, prompt)
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:64]
    expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=ttl_hours)

    try:
        async with session_ctx() as s:
            await s.execute(
                text(
                    "INSERT INTO llm_cache (cache_key, model_id, prompt_hash, response, "
                    "input_tokens, output_tokens, cost_cents, expires_at) "
                    "VALUES (:k, :m, :ph, :r, :it, :ot, :cc, :ex) "
                    "ON CONFLICT (cache_key) DO UPDATE SET "
                    "response = EXCLUDED.response, expires_at = EXCLUDED.expires_at"
                ),
                {
                    "k": key, "m": model_id, "ph": prompt_hash,
                    "r": response, "it": input_tokens, "ot": output_tokens,
                    "cc": cost_cents, "ex": expires,
                },
            )
    except Exception as exc:
        log.debug("llm_cache.set_error", error=str(exc))


def _cache_key(model_id: str, prompt: str) -> str:
    raw = f"{model_id}:{prompt}"
    return hashlib.sha256(raw.encode()).hexdigest()[:128]
