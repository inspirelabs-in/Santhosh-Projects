"""Per-stage LLM model registry — the ONE place that says which model each
pipeline stage uses.

Replaces the vague ``client.smart`` / ``client.fast`` split with explicit,
auditable per-stage assignments. Read this file to know (and change) exactly
which model scores fit, evaluates a voice call, drafts a JD, etc.

Usage in an activity:

    from src.llm.model_registry import model_for, Stage

    result = await client.complete(
        prompt=...,
        model=model_for(Stage.RESUME_FIT_SCORE),
        ...,
    )

Notes
-----
* Keys are stable stage identifiers (``Stage`` enum). The string value of each
  enum member doubles as the registry key, so config/telemetry can reference
  ``"resume_fit_score"`` directly.
* Every model here must pass the cost guard (``assert_model_allowed``); the
  module asserts this at import so a bad assignment fails fast on boot, not at
  request time.
* ``model_for`` falls back to ``DEFAULT_MODEL`` for an unmapped stage (and logs),
  so a new call site never crashes for lack of an entry — but every real stage
  SHOULD be listed here explicitly.
* An env/Langfuse override hook (``_OVERRIDES``) lets ops repoint a single stage
  without a deploy; empty by default.
"""

from __future__ import annotations

import logging
from enum import StrEnum

logger = logging.getLogger(__name__)


# --- Model identifiers (all must be cost-guard-allowed) ---------------------
# Keep these as named constants so the stage table reads cleanly and a model
# swap is a one-line change in one place.
GPT_4O_MINI = "openai/gpt-4o-mini"
GPT_41_MINI = "openai/gpt-4.1-mini"

# The default for any stage not explicitly mapped.
DEFAULT_MODEL = GPT_4O_MINI


class Stage(StrEnum):
    """Stable identifier for every LLM-backed pipeline stage.

    The string value is the registry key (also used in logs/telemetry)."""

    # --- Intake / parsing ---
    CLASSIFY_EMAIL = "classify_email"          # is-this-an-application gate
    PARSE_RESUME = "parse_resume"
    CLASSIFY_CANDIDATE_INTENT = "classify_candidate_intent"

    # --- Scoring / evaluation ---
    RESUME_FIT_SCORE = "resume_fit_score"
    SCREENING_GEN = "screening_gen"
    SCREENING_EVAL = "screening_eval"
    VOICE_SCREEN_GEN = "voice_screen_gen"
    VOICE_SCREEN_EVAL = "voice_screen_eval"    # text-fallback eval (Gemini path is separate)
    CLASSIFY_VOICEMAIL = "classify_voicemail"
    ASSIGNMENT_PARSE = "assignment_parse"
    ASSIGNMENT_EVAL = "assignment_eval"
    MEETING_ANALYSIS = "meeting_analysis"
    CANDIDATE_RANKING = "candidate_ranking"
    SCORE_OPEN_TEXT = "score_open_text"

    # --- Reports / briefs ---
    CEO_BRIEF = "ceo_brief"
    JOURNEY_REPORT = "journey_report"
    INTERVIEW_REPORT = "interview_report"

    # --- Generation / drafting ---
    REJECTION_MESSAGE = "rejection_message"
    OFFER_NOTE = "offer_note"
    ROLE_DRAFT_CHAT = "role_draft_chat"
    ROLE_SECTION_REWRITE = "role_section_rewrite"
    LINKEDIN_POST = "linkedin_post"
    ASSIGNMENT_GEN = "assignment_gen"

    # --- Recruiter Pulse agent ---
    PULSE_AGENT = "pulse_agent"                # main conversational loop
    ANSWER_CANDIDATE_QUESTION = "answer_candidate_question"


# --- The stage -> model table ------------------------------------------------
# This is the knob. Bump a single stage to a different (allowed) model here.
# Cheap deterministic classifiers + extractors -> gpt-4o-mini.
# Heavier judgment (eval, analysis, drafting, ranking) -> gpt-4.1-mini.
STAGE_MODELS: dict[Stage, str] = {
    # Intake / parsing — cheap, structured extraction.
    Stage.CLASSIFY_EMAIL: GPT_4O_MINI,
    Stage.PARSE_RESUME: GPT_4O_MINI,
    Stage.CLASSIFY_CANDIDATE_INTENT: GPT_4O_MINI,
    Stage.CLASSIFY_VOICEMAIL: GPT_4O_MINI,

    # Scoring / evaluation — judgment quality matters.
    Stage.RESUME_FIT_SCORE: GPT_41_MINI,
    Stage.SCREENING_GEN: GPT_4O_MINI,
    Stage.SCREENING_EVAL: GPT_41_MINI,
    Stage.VOICE_SCREEN_GEN: GPT_4O_MINI,
    Stage.VOICE_SCREEN_EVAL: GPT_41_MINI,
    Stage.ASSIGNMENT_PARSE: GPT_4O_MINI,
    Stage.ASSIGNMENT_EVAL: GPT_41_MINI,
    Stage.MEETING_ANALYSIS: GPT_41_MINI,
    Stage.CANDIDATE_RANKING: GPT_41_MINI,
    Stage.SCORE_OPEN_TEXT: GPT_41_MINI,

    # Reports / briefs — synthesis quality matters.
    Stage.CEO_BRIEF: GPT_41_MINI,
    Stage.JOURNEY_REPORT: GPT_41_MINI,
    Stage.INTERVIEW_REPORT: GPT_41_MINI,

    # Generation / drafting.
    Stage.REJECTION_MESSAGE: GPT_4O_MINI,
    Stage.OFFER_NOTE: GPT_4O_MINI,
    Stage.ROLE_DRAFT_CHAT: GPT_41_MINI,
    Stage.ROLE_SECTION_REWRITE: GPT_41_MINI,
    Stage.LINKEDIN_POST: GPT_4O_MINI,
    Stage.ASSIGNMENT_GEN: GPT_41_MINI,

    # Recruiter Pulse agent.
    Stage.PULSE_AGENT: GPT_41_MINI,
    Stage.ANSWER_CANDIDATE_QUESTION: GPT_4O_MINI,
}

# Ops override hook: stage_key -> model_id. Repoint a single stage without code
# changes (populate from env/Langfuse if/when wired). Validated like the table.
_OVERRIDES: dict[str, str] = {}


def model_for(stage: Stage | str) -> str:
    """Resolve the model id for a stage. Overrides win; then the table; then the
    default. The returned model is guaranteed cost-guard-allowed."""
    key = stage.value if isinstance(stage, Stage) else str(stage)

    if key in _OVERRIDES:
        return _OVERRIDES[key]

    try:
        member = Stage(key)
    except ValueError:
        logger.warning("model_for: unknown stage %r -> DEFAULT_MODEL %s", key, DEFAULT_MODEL)
        return DEFAULT_MODEL

    model = STAGE_MODELS.get(member)
    if model is None:
        logger.warning("model_for: stage %s has no model -> DEFAULT_MODEL %s", key, DEFAULT_MODEL)
        return DEFAULT_MODEL
    return model


def _validate_registry() -> None:
    """Fail fast at import if any configured model violates the cost guard.

    The cost guard lives in ``llm.client`` (which imports litellm). Import it
    lazily so the registry itself stays import-light and unit-testable without
    the LLM stack; if the guard can't be imported we skip validation (the guard
    still runs at call time inside ``client.complete``)."""
    try:
        from src.llm.client import assert_model_allowed
    except Exception:  # noqa: BLE001 - missing LLM deps in some envs/tests
        logger.debug("cost guard unavailable at import; skipping registry validation")
        return
    for model in {*STAGE_MODELS.values(), *_OVERRIDES.values(), DEFAULT_MODEL}:
        assert_model_allowed(model)


_validate_registry()
