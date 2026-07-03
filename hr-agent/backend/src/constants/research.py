"""Tunables for the org-onboarding research sub-agent (DRY — one source)."""

from __future__ import annotations

# Gemini research call budget.
RESEARCH_TIMEOUT_SECONDS: float = 45.0
RESEARCH_MAX_OUTPUT_TOKENS: int = 2048

# Enable Google Search grounding + url-context on the research call. Turn off
# to fall back to the model's parametric knowledge only (no live web).
RESEARCH_ENABLE_GROUNDING: bool = True

# A scalar persona field shorter than this is treated as "weak" (present but
# too thin to be useful) and re-surfaced as a question by setup_org_manual.
WEAK_SCALAR_MIN_CHARS: int = 12
