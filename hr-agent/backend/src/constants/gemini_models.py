"""Gemini model ids — one source, keyed by purpose.

Each Gemini-backed feature picks its model by purpose, not by a hard-coded
string scattered across the codebase. Correcting or upgrading a model id is a
one-line change here.
"""

from __future__ import annotations

GEMINI_MODELS: dict[str, str] = {
    # Voice-screen recording evaluation (audio + transcript).
    "voice_eval": "gemini-2.5-flash",
    # Org-onboarding web research (Google Search grounding + url-context).
    "org_onboarding": "gemini-3-flash-preview",
}
