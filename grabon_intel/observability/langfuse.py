"""Optional Langfuse instrumentation.

Returns a no-op client when keys are not configured so callers never need
to branch on availability. Designed to be added to `llm.router.complete`
via a context manager; failing the LLM call must NOT depend on Langfuse.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

from ..config import get_settings
from ..logging import get_logger

log = get_logger(__name__)


class _NoopClient:
    enabled = False

    def trace(self, **_: Any) -> "_NoopTrace":
        return _NoopTrace()

    def flush(self) -> None:
        pass


class _NoopTrace:
    def generation(self, **_: Any) -> "_NoopGen":
        return _NoopGen()

    def end(self, **_: Any) -> None:
        pass


class _NoopGen:
    def end(self, **_: Any) -> None:
        pass


@lru_cache(maxsize=1)
def langfuse_client() -> Any:
    s = get_settings()
    pub = s.langfuse_public_key.get_secret_value()
    sec = s.langfuse_secret_key.get_secret_value()
    if not (pub and sec):
        return _NoopClient()
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]

        client = Langfuse(public_key=pub, secret_key=sec, host=s.langfuse_host)
        # Mark enabled for callers that want to branch on real client behavior.
        setattr(client, "enabled", True)
        return client
    except Exception as exc:  # noqa: BLE001
        log.warning("langfuse.unavailable", exc=str(exc))
        return _NoopClient()


@asynccontextmanager
async def trace_llm(*, name: str, model: str, prompt: str, metadata: dict[str, Any] | None = None):
    """Async ctx manager wrapping a single LLM call.

    Yields a `record(usage, output, error)` callable. The wrapper is
    always safe to use even when Langfuse is absent.
    """
    client = langfuse_client()
    tr = client.trace(name=name, metadata=metadata or {})
    gen = tr.generation(name=name, model=model, input=prompt, metadata=metadata or {})

    state: dict[str, Any] = {"output": None, "usage": None, "error": None}

    def record(*, output: str | None = None, usage: dict[str, Any] | None = None, error: str | None = None) -> None:
        state["output"] = output
        state["usage"] = usage
        state["error"] = error

    try:
        yield record
    finally:
        try:
            gen.end(output=state["output"], usage=state["usage"], level="ERROR" if state["error"] else "DEFAULT", status_message=state["error"])
            tr.end()
        except Exception:  # noqa: BLE001
            pass
