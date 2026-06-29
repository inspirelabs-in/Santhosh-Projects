"""Langfuse Prompt Management integration with local fallback.

Fetches prompt templates from Langfuse cloud (editable by team in browser).
Falls back to local Python constants if Langfuse is unreachable or prompt
not yet uploaded. Caches prompts in-memory with TTL to avoid per-call latency.

Resilience (LF-H1/H2/M1/M5):
- Retry-with-backoff for Langfuse client init (not one-shot).
- Stale-while-revalidate: on fetch failure, keeps last good value.
- TTL 30s to narrow cross-worker mixed-version window.
- invalidate_cache clears ALL labels for a prompt, not just two.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from src.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

# ---------------------------------------------------------------------------
# In-memory cache.  TTL = 30s (reduced from 60s to narrow cross-worker
# mixed-version window; full fix for LF-H2 requires shared Redis cache).
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 30

# key -> (text, fetched_at, source)
_cache: dict[str, tuple[str, float, str]] = {}

# Langfuse client with retry-on-failure (LF-H1).
_langfuse_client: object | None = None
_last_init_attempt: float = 0.0
_consecutive_init_failures: int = 0
_INIT_RETRY_SECONDS = 60
_INIT_BACKOFF_SECONDS = 300
_INIT_BACKOFF_THRESHOLD = 3


def _get_langfuse() -> object | None:
    """Return Langfuse client, retrying init on failure with backoff (LF-H1)."""
    global _langfuse_client, _last_init_attempt, _consecutive_init_failures

    if _langfuse_client is not None:
        return _langfuse_client

    if not (_settings.langfuse_public_key and _settings.langfuse_secret_key):
        return None

    now = time.time()
    cooldown = (
        _INIT_BACKOFF_SECONDS
        if _consecutive_init_failures >= _INIT_BACKOFF_THRESHOLD
        else _INIT_RETRY_SECONDS
    )
    if _last_init_attempt and (now - _last_init_attempt) < cooldown:
        return None

    _last_init_attempt = now
    try:
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            public_key=_settings.langfuse_public_key,
            secret_key=_settings.langfuse_secret_key,
            host=_settings.langfuse_host,
        )
        _consecutive_init_failures = 0
        logger.info("Langfuse prompt client initialised (host=%s)", _settings.langfuse_host)
    except Exception as e:
        _consecutive_init_failures += 1
        logger.warning(
            "Langfuse prompt client init failed (attempt %d): %s",
            _consecutive_init_failures,
            e,
        )
    return _langfuse_client


def get_prompt(
    name: str,
    *,
    fallback: str,
    label: str = "production",
    cache_ttl: int = _CACHE_TTL_SECONDS,
) -> str:
    """Fetch a prompt from Langfuse with stale-while-revalidate (LF-M1).

    On cache miss or TTL expiry:
      1. Try Langfuse -> cache as "langfuse" source.
      2. On failure, KEEP the last fetched value if one exists.
      3. Only fall back to the hardcoded constant if never fetched.
    """
    cache_key = f"{name}:{label}"
    now = time.time()

    cached = _cache.get(cache_key)
    if cached is not None:
        cached_text, cached_at, _src = cached
        if now - cached_at < cache_ttl:
            return cached_text

    lf = _get_langfuse()
    if lf is not None:
        try:
            prompt_obj = lf.get_prompt(name, label=label, type="text")  # type: ignore[union-attr]
            text = prompt_obj.prompt
            _cache[cache_key] = (text, now, "langfuse")
            return text
        except Exception as e:
            logger.warning("Failed to fetch prompt '%s' from Langfuse: %s", name, e)

    # Stale-while-revalidate (LF-M1): keep last good value on failure.
    if cached is not None:
        _cache[cache_key] = (cached[0], now, cached[2])
        return cached[0]

    _cache[cache_key] = (fallback, now, "local")
    logger.info("Prompt '%s' using local fallback (len=%d)", name, len(fallback))
    return fallback


def compile_prompt(
    name: str,
    *,
    fallback: str,
    label: str = "production",
    **variables: Any,
) -> str:
    """Fetch prompt from Langfuse and format with variables.

    Uses regex substitution instead of str.format() so that literal JSON
    braces in the prompt (e.g. output schema examples) never cause KeyError.
    Only replaces {variable_name} tokens that match a supplied variable.
    """
    template = get_prompt(name, fallback=fallback, label=label)

    # Drift guard: warn when a supplied variable has NO slot in the template, so it
    # is silently dropped. This is exactly how a stale Langfuse prompt (missing e.g.
    # {user_brief_section}) discards data with no error — surface it loudly instead.
    present = set(re.findall(r"\{(\w+)\}", template))
    dropped = [k for k in variables if k not in present]
    if dropped:
        logger.warning(
            "compile_prompt('%s', label='%s'): supplied variables have NO slot in the "
            "template and were DROPPED: %s — the live prompt may be stale/out of sync.",
            name, label, sorted(dropped),
        )

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        return str(variables[key]) if key in variables else match.group(0)

    return re.sub(r"\{(\w+)\}", _replace, template)


def invalidate_cache(name: str | None = None) -> None:
    """Clear cached prompts. Clears ALL labels for a name (LF-M5)."""
    if name:
        prefix = f"{name}:"
        for k in [k for k in _cache if k.startswith(prefix)]:
            del _cache[k]
    else:
        _cache.clear()
    logger.info("Prompt cache invalidated: %s", name or "all")


def get_prompt_sources() -> dict[str, str]:
    """Return map of cached prompt names to their source (langfuse/local)."""
    return {k: v[2] for k, v in _cache.items()}
