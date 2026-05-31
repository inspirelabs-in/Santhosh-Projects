"""Structured logging w/ secret redaction + error categorisation.

Error categories help triage Docker console noise:
  - rate_limit: SearXNG/API 429 or CAPTCHA
  - timeout: network or LLM timeout
  - api_error: upstream API 4xx/5xx
  - activity_error: Temporal activity failure
  - config_error: missing env var / bad config
  - internal: unclassified bug
"""
from __future__ import annotations

import enum
import logging
import re
import sys
import time
from typing import Any

import structlog

from .config import get_settings

_SECRET_RE = re.compile(r"(api[_-]?key|token|password|secret|bearer)\s*[:=]\s*\S+", re.I)


class ErrorCategory(str, enum.Enum):
    RATE_LIMIT = "rate_limit"
    CAPTCHA = "captcha"
    TIMEOUT = "timeout"
    API_ERROR = "api_error"
    ACTIVITY_ERROR = "activity_error"
    CONFIG_ERROR = "config_error"
    NETWORK = "network"
    INTERNAL = "internal"


def classify_error(exc: BaseException | None = None, message: str = "") -> ErrorCategory:
    """Infer error category from exception type + message text."""
    text = f"{type(exc).__name__}: {exc}" if exc else message
    text_lower = text.lower()
    if any(k in text_lower for k in ("429", "rate limit", "too many requests", "ratelimit")):
        return ErrorCategory.RATE_LIMIT
    if any(k in text_lower for k in ("captcha", "unusual traffic", "blocked")):
        return ErrorCategory.CAPTCHA
    if any(k in text_lower for k in ("timeout", "timed out", "connecttimeout", "readtimeout")):
        return ErrorCategory.TIMEOUT
    if any(k in text_lower for k in ("not registered", "activity", "workflow")):
        return ErrorCategory.ACTIVITY_ERROR
    if any(k in text_lower for k in ("no credentials", "missing", "not configured")):
        return ErrorCategory.CONFIG_ERROR
    if any(k in text_lower for k in ("400", "401", "403", "404", "500", "502", "503")):
        return ErrorCategory.API_ERROR
    return ErrorCategory.INTERNAL


_error_buffer: list[dict[str, Any]] = []
_MAX_BUFFER = 500


def record_error(
    *,
    category: ErrorCategory | str,
    source: str,
    message: str,
    detail: str | None = None,
    brand_id: int | None = None,
) -> None:
    """Buffer a structured error for API retrieval + DB persistence."""
    entry = {
        "ts": time.time(),
        "category": category.value if isinstance(category, ErrorCategory) else category,
        "source": source,
        "message": message[:500],
        "detail": (detail or "")[:2000],
        "brand_id": brand_id,
    }
    _error_buffer.append(entry)
    if len(_error_buffer) > _MAX_BUFFER:
        _error_buffer.pop(0)


def get_recent_errors(limit: int = 50, category: str | None = None) -> list[dict[str, Any]]:
    """Return most recent buffered errors, optionally filtered by category."""
    items = _error_buffer
    if category:
        items = [e for e in items if e["category"] == category]
    return list(reversed(items[-limit:]))


def get_error_summary() -> dict[str, Any]:
    """Return counts by category + health status."""
    from collections import Counter
    now = time.time()
    recent = [e for e in _error_buffer if now - e["ts"] < 3600]
    counts = Counter(e["category"] for e in recent)
    total = len(recent)
    health = "healthy"
    if counts.get("rate_limit", 0) + counts.get("captcha", 0) > 10:
        health = "degraded"
    if total > 50:
        health = "unhealthy"
    return {
        "health": health,
        "last_hour_total": total,
        "by_category": dict(counts),
        "latest": recent[-5:] if recent else [],
    }


def _redact(_logger, _method, event_dict):
    for k, v in list(event_dict.items()):
        if isinstance(v, str):
            event_dict[k] = _SECRET_RE.sub(r"\1=***", v)
    return event_dict


def configure() -> None:
    s = get_settings()
    level = getattr(logging, s.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _redact,
    ]
    if s.log_json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    structlog.configure(processors=processors, wrapper_class=structlog.make_filtering_bound_logger(level))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
