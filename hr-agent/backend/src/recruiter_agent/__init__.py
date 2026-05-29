"""Recruiter-side chat agent.

Replaces the dashboard grid with a ChatGPT-style chat surface. The agent
runs OpenAI tool-calling against a curated set of tools that wrap the
existing dashboard endpoints + pipeline actions, so HR can:

  * "list candidates that applied this week"
  * "send a chat invite to <email>"
  * "what's stuck in screening?"
  * "create a role for Senior PM, attach this JD..."
  * "show me the journey for <candidate>"

Public surface:
    * ``run_recruiter_turn(...)``   yields SSE events
"""

from src.recruiter_agent.runner import run_recruiter_turn

__all__ = ["run_recruiter_turn"]
