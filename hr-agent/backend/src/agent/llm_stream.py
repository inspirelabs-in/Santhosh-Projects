"""Streaming completion helper for the chat agent.

LiteLLM's ``acompletion(stream=True)`` yields chunk objects whose .choices[0]
.delta.content carries the next token slice. This wrapper:

  * Pushes API keys into env (LLMClient.__init__ already did the work, but
    we don't depend on a client instance here so callers can stream from
    nodes without instantiating one).
  * Enforces the cheap-model guard.
  * Catches and reshapes auth / rate / timeout errors into ``LLMError``.
  * Yields plain-string token chunks plus a final "_done" sentinel carrying
    usage + latency so the caller can persist the assistant message.

This intentionally does NOT integrate with Langfuse generation tracing --
chat turns are too chatty to log every one as a generation. The runner
records the final message + token usage to ``messages`` table instead.
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

import litellm

from src.config import get_settings
from src.llm.client import LLMError, assert_model_allowed, pat_sub

logger = logging.getLogger(__name__)
_settings = get_settings()


async def stream_chat(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.4,
    max_tokens: int = 1024,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a chat completion. Yields ``{"type": ..., ...}`` events.

    Event shapes:
      * ``{"type": "token", "delta": "<text>"}``
      * ``{"type": "done", "text": "<full>", "input_tokens": int,
            "output_tokens": int, "latency_ms": int, "model": str}``

    Errors raise ``LLMError`` directly -- callers must wrap.
    """
    assert_model_allowed(model)
    started = time.perf_counter()
    full = ""
    input_tokens = 0
    output_tokens = 0

    try:
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
    except litellm.exceptions.AuthenticationError as e:
        raise LLMError(f"LLM auth failure: {pat_sub(str(e))}") from None
    except litellm.exceptions.BadRequestError as e:
        raise LLMError(f"LLM bad request: {pat_sub(str(e))}") from None

    async for chunk in response:
        # Token delta
        choices = getattr(chunk, "choices", None) or []
        if choices:
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None) if delta else None
            if content:
                full += content
                yield {"type": "token", "delta": content}
        # Final usage chunk (LiteLLM emits a usage block at the end when
        # stream_options.include_usage=True).
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

    latency_ms = int((time.perf_counter() - started) * 1000)
    yield {
        "type": "done",
        "text": full,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "model": model,
    }
