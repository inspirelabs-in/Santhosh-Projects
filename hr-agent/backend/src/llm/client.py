"""LiteLLM wrapper with Langfuse tracing and Pydantic-validated structured output.

Every call in the hiring agent goes through `LLMClient.complete()`. The wrapper:
  - Injects OpenAI (default) or Anthropic credentials from server env only.
  - Redacts any accidental key leak from logs before anything ships to stdout.
  - Enforces per-minute + per-day + per-candidate rate limits via Redis.
  - Asks the model for strict JSON and validates it against a Pydantic model.
  - Retries on validation / transient errors with exponential backoff.
  - Emits a Langfuse generation tied to the candidate_id + prompt version.
  - Returns a typed `LLMResult[T]` carrying the parsed output, raw text, and
    the Langfuse trace id (so callers can write it to audit_log).

Security posture:
  - The API key never leaves the server process. It is not in audit logs,
    Langfuse payloads, exception messages, or HTTP response bodies.
  - LiteLLM reads the key from os.environ. We never log os.environ.
  - If a misconfigured library tries to print the key, the redacting log
    filter (installed below) replaces it with `***`.

Never pass user prose straight into an f-string -- use the helpers in
src/llm/prompts/ which escape/truncate as needed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Generic, TypeVar
from uuid import UUID

import litellm
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import get_settings
from src.services.rate_limit import (
    RateLimitExceeded,
    check_before_call,
    record_after_call,
)

logger = logging.getLogger(__name__)
_settings = get_settings()


# ---------------------------------------------------------------------------
# Key hardening: never let a secret land in a log line.
# ---------------------------------------------------------------------------


_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),                 # OpenAI
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),             # Anthropic
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{10,}", re.IGNORECASE),
]


class _SecretRedactingFilter(logging.Filter):
    """Replace any known secret pattern in log records with `***`."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            msg = record.msg
            for pat in _SECRET_PATTERNS:
                msg = pat.sub("***REDACTED***", msg)
            record.msg = msg
        if record.args:
            try:
                args = tuple(
                    pat_sub(a) if isinstance(a, str) else a for a in record.args  # type: ignore[arg-type]
                )
                record.args = args  # type: ignore[assignment]
            except Exception:
                pass
        return True


_URL_PAT = re.compile(r"https?://\S+")
_IP_PAT = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")
_EMAIL_PAT = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def pat_sub(s: str) -> str:
    for pat in _SECRET_PATTERNS:
        s = pat.sub("***REDACTED***", s)
    s = _URL_PAT.sub("[URL]", s)
    s = _IP_PAT.sub("[IP]", s)
    s = _EMAIL_PAT.sub("[EMAIL]", s)
    return s[:500]


# Install on root, LiteLLM, httpx, urllib3, OpenAI SDK loggers.
_FILTER = _SecretRedactingFilter()
for name in ("", "litellm", "httpx", "httpcore", "urllib3", "openai", "anthropic"):
    logging.getLogger(name).addFilter(_FILTER)


# ---------------------------------------------------------------------------
# Provider credentials (server-only)
# ---------------------------------------------------------------------------


# LiteLLM reads keys from env vars. We push the configured key in, and never
# read it out again. If neither is set, LiteLLM raises AuthenticationError on
# the first call -- which we catch below and turn into an explicit `LLMError`.
if _settings.openai_api_key:
    os.environ["OPENAI_API_KEY"] = _settings.openai_api_key
if _settings.anthropic_api_key:
    os.environ["ANTHROPIC_API_KEY"] = _settings.anthropic_api_key
if _settings.groq_api_key:
    os.environ["GROQ_API_KEY"] = _settings.groq_api_key
# Ollama: LiteLLM reads OLLAMA_API_BASE / OLLAMA_BASE_URL from env.
if _settings.ollama_base_url:
    os.environ["OLLAMA_API_BASE"] = _settings.ollama_base_url
    os.environ["OLLAMA_BASE_URL"] = _settings.ollama_base_url

# ---------------------------------------------------------------------------

# Substrings that mark a model as banned. Order matters only for clarity.
_BANNED_MODEL_SUBSTRINGS: tuple[str, ...] = (
    "gpt-4o",           # bare gpt-4o (allowlist for gpt-4o-mini takes precedence)
    "gpt-4-turbo",
    "gpt-4-32k",
    "gpt-4-0",
    "gpt-4-1",
    "gpt-4.5",
    "gpt-5",            # incl. gpt-5, gpt-5-turbo (NOT gpt-5-nano/mini -- whitelisted below)
    "o1-preview",
    "o1-pro",
    "o1-mini",          # still pricier than 4o-mini
    "o1-",
    "o3-",
    "o3",
    "claude-3-opus",
    "claude-opus",
    "claude-3-5-sonnet",
    "claude-sonnet",
    "claude-3-sonnet",
    "llama-3.3-70b",
    "llama-3.1-70b",
    "llama-3.1-405b",
    "llama-70b",
    "mixtral-8x22b",
    "qwen2.5:72b",
    "qwen2.5:32b",
    "deepseek-r1",
)

# Explicit allowlist takes precedence (useful when a banned substring
# would otherwise catch a cheap variant, e.g. "gpt-5-nano" if released).
_ALLOWED_MODEL_SUBSTRINGS: tuple[str, ...] = (
    "gpt-4o",  # opted in for the high-value scoring stages (fit) + JD drafting; deliberate cost choice
    "gpt-4o-mini",
    "gpt-4.1-nano",
    "gpt-4.1-mini",
    "gpt-5-nano",
    "gpt-5-mini",
    "qwen2.5:3b",
    "groq/llama-3.1-8b-instant",
)


def _bare_model_name(model_id: str) -> str:
    """Strip ``provider/`` prefix and lowercase for matching."""
    name = model_id.lower().strip()
    if "/" in name:
        name = name.split("/", 1)[1]
    return name


def assert_model_allowed(model_id: str) -> None:
    """Raise LLMError if ``model_id`` is on the premium ban-list.

    Allowlist matches first (cheap variants escape the ban). All other
    matches against ``_BANNED_MODEL_SUBSTRINGS`` raise.
    """
    bare = _bare_model_name(model_id)
    if any(allowed in bare for allowed in _ALLOWED_MODEL_SUBSTRINGS):
        return
    for banned in _BANNED_MODEL_SUBSTRINGS:
        if banned in bare:
            raise LLMError(
                f"Premium model {model_id!r} blocked by cost guard. "
                f"Use a cheap model: {', '.join(_ALLOWED_MODEL_SUBSTRINGS[:4])}, ..."
            )


litellm.drop_params = True  # silently ignore unsupported params across providers
# Never log the request/response bodies at the LiteLLM level -- they can
# contain candidate PII, and we handle logging ourselves via Langfuse.
litellm.suppress_debug_info = True
litellm.set_verbose = False

TModel = TypeVar("TModel", bound=BaseModel)


class LLMError(RuntimeError):
    """Non-retryable LLM error (e.g. quota exhausted, auth failure)."""


class LLMParseError(RuntimeError):
    """Model produced output that could not be parsed into the target Pydantic model.

    Retryable up to `max_attempts`.
    """


@dataclass
class LLMResult(Generic[TModel]):
    parsed: TModel
    raw_text: str
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    trace_id: str | None
    generation_id: str | None


# ---------------------------------------------------------------------------
# Langfuse via LiteLLM callback (Langfuse 4.x compatible)
# ---------------------------------------------------------------------------

# Langfuse 4.x compatibility shims for LiteLLM's bundled adapter.
#
# 1. `langfuse.version.__version__` — sub-module removed in 4.x; LiteLLM
#    reads it to decide which kwargs to pass. We recreate the module.
#
# 2. `sdk_integration` kwarg — LiteLLM passes this to Langfuse() but
#    4.x doesn't accept it. We wrap the constructor to silently drop
#    unknown kwargs so `safe_init_langfuse_client` doesn't explode.
try:
    import langfuse as _langfuse_pkg
    import sys, types, functools, inspect

    # Shim 1: restore langfuse.version module
    if not hasattr(_langfuse_pkg, "version"):
        _compat = types.ModuleType("langfuse.version")
        _compat.__version__ = _langfuse_pkg.__version__
        _langfuse_pkg.version = _compat  # type: ignore[attr-defined]
        sys.modules["langfuse.version"] = _compat

    # Shim 2: make Langfuse() accept (and ignore) unknown kwargs
    _orig_init = _langfuse_pkg.Langfuse.__init__
    _valid_params = set(inspect.signature(_orig_init).parameters.keys())

    @functools.wraps(_orig_init)
    def _patched_init(self, *args, **kwargs):
        filtered = {k: v for k, v in kwargs.items() if k in _valid_params}
        return _orig_init(self, *args, **filtered)

    _langfuse_pkg.Langfuse.__init__ = _patched_init  # type: ignore[method-assign]
except Exception as exc:
    logger.debug("Langfuse compat shim skipped: %s", exc)

_langfuse_enabled = False

if _settings.langfuse_public_key and _settings.langfuse_secret_key:
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", _settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", _settings.langfuse_secret_key)
    if _settings.langfuse_host:
        os.environ.setdefault("LANGFUSE_HOST", _settings.langfuse_host)
    try:
        litellm.success_callback = list(set(litellm.success_callback or []) | {"langfuse"})
        litellm.failure_callback = list(set(litellm.failure_callback or []) | {"langfuse"})
        from litellm.integrations.langfuse.langfuse import LangFuseLogger
        LangFuseLogger()
        _langfuse_enabled = True
        logger.info("Langfuse tracing enabled via LiteLLM callback")
    except Exception as exc:
        logger.warning("Langfuse callback failed to initialise (%s); tracing disabled", exc)
        litellm.success_callback = [c for c in (litellm.success_callback or []) if c != "langfuse"]
        litellm.failure_callback = [c for c in (litellm.failure_callback or []) if c != "langfuse"]


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Pull a JSON object out of an LLM response.

    Handles: raw JSON, JSON fenced in ```json ... ```, and JSON embedded with
    leading/trailing prose. Raises ValueError if nothing parseable is found.
    """
    text = text.strip()
    # Try straight parse first.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fenced block.
    if match := _JSON_BLOCK_RE.search(text):
        return json.loads(match.group(1))
    # Greedy -- first "{" or "[" to matching last "}" or "]".
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
    raise ValueError("No JSON object found in model output")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LLMClient:
    """Thin wrapper around LiteLLM with structured-output enforcement + tracing."""

    def __init__(
        self,
        default_fast_model: str | None = None,
        default_smart_model: str | None = None,
    ) -> None:
        self.fast = default_fast_model or _settings.llm_model_fast
        self.smart = default_smart_model or _settings.llm_model_smart
        assert_model_allowed(self.fast)
        assert_model_allowed(self.smart)

    async def complete(
        self,
        *,
        prompt: str,
        response_model: type[TModel],
        model: str | None = None,
        trace_name: str,
        prompt_version: str,
        candidate_id: UUID | None = None,
        application_id: UUID | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        max_attempts: int = 3,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResult[TModel]:
        """Run a prompt and return a validated Pydantic object.

        Retries on JSON parse / Pydantic validation failures, and on litellm's
        transient APIConnectionError / RateLimitError / Timeout.
        """
        model_id = model or self.fast
        assert_model_allowed(model_id)
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # Rate limits / spend caps (Redis-backed). Fails fast before we make
        # the network call so we don't even pay an OpenAI token for a call
        # that wouldn't have been allowed.
        try:
            await check_before_call(
                candidate_id=candidate_id,
                model=model_id,
                est_output_tokens=max_tokens,
            )
        except RateLimitExceeded as e:
            raise LLMError(f"LLM rate limit hit: {e}") from e

        # Langfuse metadata passed through LiteLLM's callback system.
        lf_metadata: dict[str, Any] = {
            "trace_name": trace_name,
            "candidate_id": str(candidate_id) if candidate_id else None,
            "application_id": str(application_id) if application_id else None,
            "prompt_version": prompt_version,
            **(metadata or {}),
        }
        trace_id: str | None = None
        generation_id: str | None = None

        last_error: Exception | None = None
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8.0),
            retry=retry_if_exception_type((LLMParseError, litellm.exceptions.APIConnectionError, litellm.exceptions.RateLimitError, litellm.exceptions.Timeout)),
            reraise=True,
        ):
            with attempt:
                try:
                    response = await litellm.acompletion(
                        model=model_id,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_format={"type": "json_object"},
                        metadata=lf_metadata,
                    )
                except litellm.exceptions.AuthenticationError as e:
                    last_error = e
                    safe = pat_sub(str(e))
                    raise LLMError(f"LLM auth failure: {safe}") from None
                except litellm.exceptions.BadRequestError as e:
                    last_error = e
                    safe = pat_sub(str(e))
                    raise LLMError(f"LLM bad request: {safe}") from None

                raw_text: str = response.choices[0].message.content or ""
                usage = getattr(response, "usage", None)
                input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                latency_ms = int(
                    float(response._response_ms) if hasattr(response, "_response_ms") else 0
                )

                # Extract trace/generation IDs from LiteLLM response if available.
                lf_log = getattr(response, "_hidden_params", {}).get("litellm_logging_obj")
                if lf_log and hasattr(lf_log, "model_call_details"):
                    trace_id = lf_log.model_call_details.get("litellm_trace_id")

                try:
                    payload = extract_json(raw_text)
                    parsed = response_model.model_validate(payload)
                except (ValueError, json.JSONDecodeError, ValidationError) as e:
                    last_error = e
                    logger.warning(
                        "LLM output failed validation (attempt %s): %s",
                        attempt.retry_state.attempt_number,
                        e,
                    )
                    raise LLMParseError(str(e)) from e

                await record_after_call(
                    model=model_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

                return LLMResult(
                    parsed=parsed,
                    raw_text=raw_text,
                    model=model_id,
                    prompt_version=prompt_version,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    trace_id=trace_id,
                    generation_id=generation_id,
                )

        raise LLMError(f"LLM call exhausted retries: {last_error}")


# Module-level singleton for convenience.
_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
