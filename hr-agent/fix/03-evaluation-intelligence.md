# EVAL — Evaluation Intelligence (persona → generated role specs → rubric-driven scoring)

> **STATUS (2026-06-22): BACKEND DONE.** Company persona + per-role `evaluation_spec`/`company_context` generated at JD time; every scoring prompt (fit/screening/voice/assignment/meeting) reads the role spec via `scoring_context.scoring_prompt_vars()`. Hardcoded GrabOn weights removed.

> **The core issue:** scoring criteria are static and wrong-by-default. Judging a
> **coupon editor** on "builder > spectator mindset" is a category error — that
> mindset doesn't belong to that role. Dimensions, weights, and "what to look
> for" must be **role-specific and generated**, grounded in a **company persona**,
> editable by the user, and every scoring prompt must read them instead of
> hardcoded values. And the reasoning behind scores must be **profound and
> evidence-grounded**, not generic.
>
> Ships with SP2 (needs the new `company_persona` and `roles.evaluation_spec`).

---

## 1. The three layers

```
COMPANY PERSONA  (DB, static, editable — your worldview)
        │  seeds, at JD creation time
        ▼
ROLE EVALUATION SPEC  (generated per role from persona + JD, user-editable)
   dimensions · weights · "what good looks like" signals · anti-signals · knockouts
        │  parameterizes EVERY scoring prompt
        ▼
RUBRIC-DRIVEN SCORING  (fit · screening · voice · assignment · each interview)
   per-dimension: signal looked for → evidence cited → judgment → score → confidence
```

Today `FIT_SCORE_V1` ships hardcoded weights (`skills=40, experience=25, ctc=20,
logistics=15`) and fixed dimensions for *every* role. `MEETING_ANALYSIS_V1`
scores `technical/communication/confidence` regardless of whether the role is
technical. That's the bug. The fix is to make the **role's generated spec** the
only source of dimensions and weights.

---

## 2. `company_persona` (new table, org-scoped, static-but-editable)

One per org (tenant-ready: each customer org gets its own worldview — a genuine
SaaS differentiator). Structured JSONB sections, edited in Settings.

| Field | Purpose |
|---|---|
| id, org_id | |
| company_name, mission, domain_context | who you are, what business you're in |
| values | the values you hire for (list, with descriptions) |
| what_good_looks_like | general signals of a strong hire here |
| anti_patterns | general red flags / who does *not* fit |
| hiring_philosophy | tone, bar, how you weigh potential vs experience |
| tone | voice for candidate-facing comms |
| version, updated_at, updated_by | |

The persona is **not** a rubric. It is the seed the generator reasons *from*. A
coupon-editor spec and a backend-engineer spec are both generated from the same
persona but land on completely different dimensions — because the generator is
told "derive what matters *for this role*, do not assume engineering defaults."

---

## 3. `roles.evaluation_spec` (generated, editable, versioned JSONB)

Generated at JD drafting (a new sub-prompt in the role-drafting flow) from
`(company_persona + role title + jd_text + seniority + employment_type)`.

```jsonc
{
  "dimensions": [
    {
      "key": "editorial_judgment",
      "label": "Editorial judgment",
      "weight": 30,
      "what_good_looks_like": [
        "Catches misleading or expired offers without prompting",
        "Writes coupon copy that is accurate and scannable"
      ],
      "anti_signals": ["Prioritizes volume over accuracy", "Misses edge cases in terms"],
      "applies_to": ["fit", "screening", "assignment", "interview:editor"]
    },
    { "key": "accuracy_attention", "label": "Accuracy & attention to detail", "weight": 30, ... },
    { "key": "throughput", "label": "Throughput under volume", "weight": 20, ... },
    { "key": "domain_familiarity", "label": "Deals/coupons domain sense", "weight": 20, ... }
  ],
  "knockouts": [
    {"key": "no_relevant_writing", "rule": "No editorial/content writing experience at all"}
  ],
  "stage_rubrics": {
    "fit": "weight dimensions as above against the resume",
    "interview:ceo": "focus on ownership and judgment, not coding"
  },
  "generated_from_persona_version": 4,
  "edited_by_user": false
}
```

- **No "builder mindset" unless the persona + role warrant it.** The generator is
  explicitly instructed that engineering dimensions are *not* defaults.
- **User edits before confirming** in the JD dialog (the dialog already shows a
  similar panel — now it's backed by this object and actually saved/used).
- Weights must sum to 100; validated on save.
- `applies_to` lets a dimension target only the stages where it can be observed.

---

## 4. Rubric-driven scoring prompts (kill the hardcoded defaults)

Refactor each scoring prompt to take `evaluation_spec` as input and score
**only the spec's dimensions** with the spec's weights:

| Prompt | Change |
|---|---|
| `FIT_SCORE_V1` | Remove hardcoded `*_weight` params; iterate `evaluation_spec.dimensions`; apply `knockouts`. |
| `SCREENING_EVAL_V1` / `SCORE_OPEN_TEXT_V1` | Score against the spec's relevant dimensions, not a fixed set. |
| `VOICE_SCREEN_EVAL_V1` | Same; verdict thresholds read from spec/role config, not constants. |
| `ASSIGNMENT_PARSE_V1` | Evaluate against the spec's `what_good_looks_like` for the role. |
| `MEETING_ANALYSIS_V1` | Replace fixed `technical/communication/confidence` with the spec dimensions that `applies_to` this interview stage. (Fixes `PR-10`.) |

All prompt versions bump (e.g. `FIT_SCORE_V2`) and remain Langfuse-managed.

---

## 5. Profound, evidence-grounded reasoning

Per-dimension structured rationale instead of a one-liner:

```jsonc
{
  "dimension": "editorial_judgment",
  "score": 7,
  "signal_looked_for": "Catches misleading/expired offers; accurate scannable copy",
  "evidence": [
    {"quote": "Reduced expired-coupon complaints 40% by adding a verification step", "source": "resume:work_history[0]"}
  ],
  "reasoning": "Demonstrates proactive accuracy ownership with a measurable outcome; no evidence of high-volume throughput, so judgment > speed is unproven.",
  "confidence": 0.7,
  "evidence_sufficiency": "partial"
}
```

Rules baked into the prompts:
- **Cite or abstain.** Every claim references evidence (a resume quote, a
  transcript line, an assignment artifact). If a dimension has no evidence, output
  `evidence_sufficiency: "none"` and a low confidence — **never invent**. This
  directly closes the hallucination traps (`PR-3,5,6,7,8,9,12`) where missing data
  produced fabricated content.
- **Anchor to the spec**, not generic competencies.
- **Confidence is first-class** and surfaces in the UI (and gates auto-advance:
  low-confidence auto decisions can be downgraded to a human action item).

---

## 6. Implementation plan

1. `company_persona` table + ORM + Settings CRUD endpoint; seed a draft persona
   from existing company/role context during the SP2 backfill (user reviews it).
2. `roles.evaluation_spec` column + validated Pydantic schema (weights sum to 100,
   dimension keys unique).
3. New role-drafting sub-prompt `ROLE_EVAL_SPEC_GEN_V1`: persona + role →
   `evaluation_spec`. Wire it into the JD dialog so the user edits and saves it
   (replaces the dead config the dialog currently collects).
4. Refactor the five scoring prompts to be spec-driven; bump versions; update the
   activity callers to pass `role.evaluation_spec`.
5. Add the structured per-dimension rationale schema to each scorer's
   `response_model`; enforce cite-or-abstain.
6. Backfill: generate an `evaluation_spec` for existing open roles (user can
   regenerate/edit). Roles without a spec fall back to a sensible generic spec
   *with a visible "ungoverned" flag* — never silently to "builder mindset."

## Acceptance criteria

- A coupon-editor role generates editorial/accuracy/throughput dimensions and
  **zero engineering-mindset dimensions**.
- No scoring prompt contains hardcoded dimension weights.
- Every score carries per-dimension evidence + confidence; missing-evidence
  dimensions are flagged, not fabricated.
- Editing a role's spec changes subsequent candidates' scoring.
- Each org's roles are scored through that org's persona.
