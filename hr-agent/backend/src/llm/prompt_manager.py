"""Langfuse Prompt Management integration with local fallback.

Fetches prompt templates from Langfuse cloud (editable by team in browser).
Falls back to local Python constants if Langfuse is unreachable or prompt
not yet uploaded. Caches prompts in-memory with TTL to avoid per-call latency.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langfuse import Langfuse

from src.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

# ---------------------------------------------------------------------------
# In-memory cache: avoids hitting Langfuse API on every LLM call.
# TTL = 60s means team edits propagate within ~1 minute without restart.
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 60

_cache: dict[str, tuple[str, float, str]] = {}  # key -> (text, timestamp, source)

_langfuse_client: Langfuse | None = None
_langfuse_init_attempted = False


def _get_langfuse() -> Langfuse | None:
    global _langfuse_client, _langfuse_init_attempted
    if _langfuse_init_attempted:
        return _langfuse_client
    _langfuse_init_attempted = True
    if not (_settings.langfuse_public_key and _settings.langfuse_secret_key):
        logger.info("Langfuse prompt management disabled (no credentials)")
        return None
    try:
        _langfuse_client = Langfuse(
            public_key=_settings.langfuse_public_key,
            secret_key=_settings.langfuse_secret_key,
            host=_settings.langfuse_host,
        )
        logger.info("Langfuse prompt client initialised (host=%s)", _settings.langfuse_host)
    except Exception as e:
        logger.warning("Langfuse prompt client failed to initialise: %s", e)
    return _langfuse_client


def get_prompt(
    name: str,
    *,
    fallback: str,
    label: str = "production",
    cache_ttl: int = _CACHE_TTL_SECONDS,
) -> str:
    """Fetch a prompt template from Langfuse, with local fallback."""
    cache_key = f"{name}:{label}"
    now = time.time()

    if cache_key in _cache:
        cached_text, cached_at, cached_source = _cache[cache_key]
        if now - cached_at < cache_ttl:
            return cached_text

    lf = _get_langfuse()
    if lf is not None:
        try:
            prompt_obj = lf.get_prompt(name, label=label, type="text")
            text = prompt_obj.prompt
            _cache[cache_key] = (text, now, "langfuse")
            logger.info("Prompt '%s' loaded from Langfuse (label=%s, len=%d)", name, label, len(text))
            return text
        except Exception as e:
            logger.warning(
                "Failed to fetch prompt '%s' from Langfuse, using local fallback: %s",
                name,
                e,
            )

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
    """Fetch prompt from Langfuse and format with variables."""
    template = get_prompt(name, fallback=fallback, label=label)
    return template.format(**variables)


def invalidate_cache(name: str | None = None) -> None:
    """Clear cached prompts. Call after manual Langfuse update if impatient."""
    if name:
        _cache.pop(f"{name}:production", None)
        _cache.pop(f"{name}:latest", None)
    else:
        _cache.clear()
    logger.info("Prompt cache invalidated: %s", name or "all")


def get_prompt_sources() -> dict[str, str]:
    """Return map of cached prompt names to their source (langfuse/local)."""
    return {k: v[2] for k, v in _cache.items()}
