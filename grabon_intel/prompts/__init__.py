"""Prompt registry. Versioned in `prompts` table; loader caches active
version per name. Eval harness must pass before flipping `active`.
"""
from .loader import get_prompt, set_active_version, upsert_prompt

__all__ = ["get_prompt", "upsert_prompt", "set_active_version"]
