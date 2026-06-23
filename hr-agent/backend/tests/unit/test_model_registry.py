"""Unit test for the per-stage LLM model registry.

Locks the contract that every pipeline stage resolves to a concrete, cost-guard-
allowed model, and that the resolver behaves (overrides, default fallback).
"""

from __future__ import annotations

from src.llm.model_registry import (
    DEFAULT_MODEL,
    STAGE_MODELS,
    Stage,
    model_for,
)

# Cost-guard allowlist mirror (kept tiny + local so this test needs no LLM deps).
# If client._ALLOWED_MODEL_SUBSTRINGS grows, this is a cheap independent check.
_ALLOWED_SUBSTRINGS = (
    "gpt-4o",  # opted in for fit + JD drafting (deliberate higher-judgment choice)
    "gpt-4o-mini", "gpt-3.5-turbo", "gpt-4.1-nano", "gpt-4.1-mini",
    "gpt-5-nano", "gpt-5-mini", "claude-haiku", "claude-3-haiku",
    "llama-3.1-8b", "llama-3-8b", "qwen2.5:7b", "qwen2.5:3b",
)


def _bare(model_id: str) -> str:
    name = model_id.lower().strip()
    return name.split("/", 1)[1] if "/" in name else name


def test_every_stage_has_a_model():
    """No stage may be left unmapped -- that's the whole point of the registry."""
    missing = [s for s in Stage if s not in STAGE_MODELS]
    assert not missing, f"stages with no model assignment: {missing}"


def test_every_model_is_a_cheap_allowed_model():
    """A misconfigured (expensive) model must never reach the table."""
    for stage, model in {**{s: m for s, m in STAGE_MODELS.items()},
                         "__default__": DEFAULT_MODEL}.items():
        bare = _bare(model)
        assert any(a in bare for a in _ALLOWED_SUBSTRINGS), f"{stage}: {model} not allowed"


def test_model_for_resolves_by_enum_and_string():
    assert model_for(Stage.RESUME_FIT_SCORE) == STAGE_MODELS[Stage.RESUME_FIT_SCORE]
    # string key (the enum value) resolves identically
    assert model_for("resume_fit_score") == STAGE_MODELS[Stage.RESUME_FIT_SCORE]


def test_model_for_unknown_stage_falls_back_to_default():
    assert model_for("totally_made_up_stage") == DEFAULT_MODEL


def test_known_stage_assignments():
    """Spot-check the explicit mapping: fit + JD drafting use the higher-judgment
    gpt-4o (the two fairness-critical stages); classifiers stay on mini."""
    assert model_for(Stage.RESUME_FIT_SCORE) == "openai/gpt-4o"
    assert model_for(Stage.ROLE_DRAFT_CHAT) == "openai/gpt-4o"
    assert model_for(Stage.CLASSIFY_EMAIL) == "openai/gpt-4o-mini"
    assert model_for(Stage.PARSE_RESUME) == "openai/gpt-4o-mini"
