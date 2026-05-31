"""Langfuse wrap. No-op when keys not set, real client when configured."""
from .langfuse import langfuse_client, trace_llm

__all__ = ["langfuse_client", "trace_llm"]
