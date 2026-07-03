"""Org-onboarding: the schema↔questions map + persona merge logic.

One source for two deterministic concerns used by the Pulse org tools:

  * ``PERSONA_FIELD_GUIDE`` + ``build_setup_questions`` — turn the current
    (possibly empty) ``HiringPersona`` into a structured interview guide for the
    gaps only. No LLM: the questions are stable copy derived from the schema.
  * ``merge_persona_patch`` — deep-merge a partial persona into what's stored
    (scalars overwrite-if-present; list fields append+dedupe by default). Never
    a blind whole-schema replace.

The guide keys are asserted at import to match ``HiringPersona`` (minus the
managed ``version``/``updated_at`` fields), so the two can't silently drift.
"""

from __future__ import annotations

from typing import Any

from src.constants.research import WEAK_SCALAR_MIN_CHARS
from src.models.persona import HiringPersona

# Fields the human/agent fills. ``version`` + ``updated_at`` are managed by the
# writer and never asked about.
_MANAGED_FIELDS = {"version", "updated_at"}

# Scalar (str|None) fields vs list fields — drives weak-detection and merge.
SCALAR_FIELDS = ("company_name", "mission", "domain_context", "hiring_philosophy", "tone")
LIST_FIELDS = ("values", "what_good_looks_like", "anti_patterns")

# Scalars where a short value legitimately means "thin, ask again". Names and
# tone are naturally short ("Meta", "warm") — present counts as filled for those.
WEAK_CHECKED_SCALARS = ("mission", "domain_context", "hiring_philosophy")


# Per-field interview guide. ``kind``: "text" (scalar) | "list" (list[str]) |
# "values" (list[{name, description}]).
PERSONA_FIELD_GUIDE: dict[str, dict[str, str]] = {
    "company_name": {
        "label": "Company name",
        "question": "What's the company called (the name candidates should see)?",
        "why": "Used verbatim in every candidate-facing email and chat.",
        "example": "e.g. 'GrabOn'",
        "kind": "text",
    },
    "mission": {
        "label": "Mission",
        "question": "What's the company's mission — the change you're trying to make?",
        "why": "Grounds candidate messaging and the hiring bar.",
        "example": "e.g. 'Make saving money effortless for every Indian shopper.'",
        "kind": "text",
    },
    "domain_context": {
        "label": "Domain context",
        "question": "What does the business actually do, and what stage is it at?",
        "why": "Lets the scorer judge candidates against your real domain, not a generic one.",
        "example": "e.g. 'B2C coupons & deals marketplace, profitable, ~150 people.'",
        "kind": "text",
    },
    "values": {
        "label": "Values",
        "question": "What are the 3–5 values you actually hire and fire on?",
        "why": "Drives the evaluation-spec dimensions generated for every role.",
        "example": "e.g. 'Ownership — sees things through without being asked.'",
        "kind": "values",
    },
    "what_good_looks_like": {
        "label": "What good looks like",
        "question": "What signals tell you someone will thrive here?",
        "why": "Positive signals scorers look for across screening and interviews.",
        "example": "e.g. 'Ships small and often; learns from users, not opinions.'",
        "kind": "list",
    },
    "anti_patterns": {
        "label": "Anti-patterns",
        "question": "What red flags or behaviours are dealbreakers for you?",
        "why": "Negative signals that should pull a candidate's score down.",
        "example": "e.g. 'Talks in abstractions, no concrete ownership of outcomes.'",
        "kind": "list",
    },
    "hiring_philosophy": {
        "label": "Hiring philosophy",
        "question": "How do you think about hiring — your approach or bar?",
        "why": "Sets the overall tone and strictness of evaluation.",
        "example": "e.g. 'Hire for slope over intercept; raw ability beats a resume.'",
        "kind": "text",
    },
    "tone": {
        "label": "Tone",
        "question": "How should the company come across to candidates?",
        "why": "Shapes the voice of candidate-facing communication.",
        "example": "e.g. 'Warm, direct, no corporate fluff.'",
        "kind": "text",
    },
}

# Guard: the guide must cover exactly the askable persona fields. Fails loudly
# at import if the schema changes and the guide isn't updated.
_askable = set(HiringPersona.model_fields) - _MANAGED_FIELDS
assert set(PERSONA_FIELD_GUIDE) == _askable, (
    "PERSONA_FIELD_GUIDE drifted from HiringPersona: "
    f"missing={_askable - set(PERSONA_FIELD_GUIDE)} "
    f"extra={set(PERSONA_FIELD_GUIDE) - _askable}"
)


def _field_status(field: str, persona: dict[str, Any]) -> str:
    """Return 'empty' | 'weak' | 'filled' for a persona field."""
    value = persona.get(field)
    if field in LIST_FIELDS:
        return "filled" if isinstance(value, list) and len(value) > 0 else "empty"
    # scalar
    if not isinstance(value, str) or not value.strip():
        return "empty"
    if field in WEAK_CHECKED_SCALARS and len(value.strip()) < WEAK_SCALAR_MIN_CHARS:
        return "weak"
    return "filled"


def build_setup_questions(persona: dict[str, Any] | None) -> dict[str, Any]:
    """Structured interview guide for the empty/weak fields of ``persona``.

    ``persona`` is the raw ``organizations.hiring_persona`` dict (may be empty).
    Returns ``{coverage, ask[], already_have[]}``; the caller adds ``org_name``.
    """
    persona = persona if isinstance(persona, dict) else {}
    ask: list[dict[str, Any]] = []
    already_have: list[str] = []
    for field, guide in PERSONA_FIELD_GUIDE.items():
        status = _field_status(field, persona)
        if status == "filled":
            already_have.append(field)
            continue
        ask.append({"field": field, "status": status, **guide})
    total = len(PERSONA_FIELD_GUIDE)
    filled = len(already_have)
    return {
        "coverage": f"{filled}/{total} fields set",
        "ask": ask,
        "already_have": already_have,
    }


def _dedupe_str_list(existing: list[Any], incoming: list[Any]) -> list[str]:
    """Append incoming strings to existing, order-preserving, case-insensitive dedupe."""
    out: list[str] = [s for s in existing if isinstance(s, str) and s.strip()]
    seen = {s.strip().lower() for s in out}
    for s in incoming:
        if not isinstance(s, str) or not s.strip():
            continue
        key = s.strip().lower()
        if key not in seen:
            out.append(s.strip())
            seen.add(key)
    return out


def _merge_values(existing: list[Any], incoming: list[Any]) -> list[dict[str, Any]]:
    """Merge value objects ({name, description}); dedupe by name, fill blank descriptions."""
    out: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for v in existing:
        if isinstance(v, dict) and isinstance(v.get("name"), str) and v["name"].strip():
            index[v["name"].strip().lower()] = len(out)
            out.append({"name": v["name"].strip(), "description": v.get("description")})
    for v in incoming:
        if not isinstance(v, dict) or not isinstance(v.get("name"), str) or not v["name"].strip():
            continue
        key = v["name"].strip().lower()
        desc = v.get("description")
        if key in index:
            # Fill a missing description from the incoming value; don't clobber.
            cur = out[index[key]]
            if not cur.get("description") and isinstance(desc, str) and desc.strip():
                cur["description"] = desc.strip()
        else:
            index[key] = len(out)
            out.append({"name": v["name"].strip(), "description": desc})
    return out


def merge_persona_patch(
    current: dict[str, Any] | None,
    patch: dict[str, Any] | None,
    *,
    replace_lists: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Deep-merge ``patch`` into ``current``; return (merged, changed_fields).

    Scalars overwrite when the patch value is a non-empty string. List fields
    append+dedupe by default, or overwrite when ``replace_lists`` is True. The
    managed ``version``/``updated_at`` fields are ignored here (set by the
    writer). The merged dict is NOT yet validated — the caller runs it through
    ``HiringPersona`` before persisting.
    """
    merged: dict[str, Any] = dict(current) if isinstance(current, dict) else {}
    patch = patch if isinstance(patch, dict) else {}
    changed: list[str] = []

    for field in SCALAR_FIELDS:
        if field not in patch:
            continue
        value = patch[field]
        if isinstance(value, str) and value.strip():
            new_val = value.strip()
            if merged.get(field) != new_val:
                merged[field] = new_val
                changed.append(field)

    for field in LIST_FIELDS:
        if field not in patch:
            continue
        incoming = patch[field]
        if not isinstance(incoming, list):
            continue
        existing = merged.get(field) if isinstance(merged.get(field), list) else []
        if replace_lists:
            new_list = (
                _merge_values([], incoming)
                if field == "values"
                else _dedupe_str_list([], incoming)
            )
        else:
            new_list = (
                _merge_values(existing, incoming)
                if field == "values"
                else _dedupe_str_list(existing, incoming)
            )
        if new_list != existing:
            merged[field] = new_list
            changed.append(field)

    return merged, changed
