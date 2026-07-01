# JD Creation & Assignment Generation — Bug Analysis

## Architecture Overview

```
RECRUITER CHAT
    │
    ▼
Pulse LLM ──► propose_role_draft() ──► _complete_draft_from_context()
    │                                       │
    │                                       ├── JD_GENERATION prompt
    │                                       ├── CONVERSATION (last 20 exchanges)
    │                                       ├── PARTIAL DATA (LLM's own args, usually {})
    │                                       │
    │                                       ▼
    │                                   RoleDraftContent (artifact stored in DB)
    │                                       │
    │                           ┌───────────┤
    │                           ▼           ▼
    │                    assignment.brief  jd_text, evaluation_spec,
    │                     (from chat)      company_context, pipeline, etc.
    │
    ├── create_role_with_assignment() ──► create_role() ──► Role(title, jd_text, eval_spec...)
    │       ↑ LLM may/may not pass `brief`
    │       ↑ assignment_brief is NOT auto-populated from the artifact
    │
    └── generate_assignment_for_role() ──► gen_assignment() ──► ASSIGNMENT_GEN prompt
                ↑ role.assignment_brief is SOURCE OF TRUTH
                ↑ (but empty when LLM forgot to pass brief to create_role)
```

## Bug 1 — JD prompt has no structured extraction (jd_generation.py)

**File:** `backend/src/llm/prompts/jd_generation.py`

**Problem:** The JD prompt is a single 63-line system message that tells the LLM to produce a `RoleDraftContent` JSON. The only instruction to respect recruiter input is one line at the very end:

> *"Honor explicit input: if the recruiter stated a comp band, location, notice period, or any other field, use that value exactly. Infer sensible defaults only for fields the conversation left unsaid."*

This is too weak. The LLM has tens of lines of JSON schema instructions before it, and the conversation transcript is appended as raw text at the end. There is no **structured extraction step** that forces the LLM to first identify what the recruiter explicitly stated before generating the JD.

**Fix:** Add a multi-step extraction block at the TOP of the instructions:

```
## EXTRACTION — FIRST, extract these from the conversation:

1. CTC band — look for explicit numbers (e.g. "12-15 LPA")
2. Location — city or remote
3. Remote policy — onsite/hybrid/remote
4. Notice period — max days
5. Assignment brief — any take-home/assignment ideas the recruiter described
6. Pipeline stages — stages the recruiter mentioned
7. Evaluation criteria — any specific skills/knowledge areas mentioned

THEN use these extracted values to fill the JSON below. Values NOT found in conversation → use smart defaults.
```

---

## Bug 2 — PARTIAL DATA is always empty (tools.py:1923-1924)

**File:** `backend/src/recruiter_agent/tools.py`, line 1923-1924

**Code:**
```python
+ f"\nPARTIAL DATA ALREADY KNOWN (merge these in, fill the rest):\n"
+ json.dumps(partial, indent=2)
```

**Problem:** Pulse is instructed (prompts.py:82) to call `propose_role_draft()` with NO arguments:
```
propose_role_draft()   ← NO arguments. The system reads the full conversation
                          and writes the complete draft
```

So `partial` is almost always `{}` — empty dict. The LLM gets "PARTIAL DATA ALREADY KNOWN: {}" which is meaningless. The JD generator gets zero structured input and must free-form everything from the raw conversation transcript.

Meanwhile, `smart_defaults_for_role` exists as a tool (schemas.py:572-582) that computes sensible defaults from existing roles, but Pulse never calls it before `propose_role_draft`.

**Fix:** Call `smart_defaults_for_role` before `_complete_draft_from_context` and pass its result as the `partial` data. This gives the JD generator real, data-driven defaults for comp, location, remote policy, etc.

---

## Bug 3 — Brief captured in artifact is lost between tools (no auto-population)

**Files:** `backend/src/recruiter_agent/tools.py` (propose_role_draft + create_role_with_assignment)
**File:** `backend/src/recruiter_agent/runner.py` (tool dispatch)

**Problem flow:**
1. `propose_role_draft` → JD generator captures `assignment.brief` from conversation → stored in artifact
2. Pulse later calls `create_role_with_assignment` with `brief` parameter (schema line 515)
3. The LLM is the **middleman** — it must re-read the artifact content from the previous tool result and re-pass `brief` to `create_role_with_assignment`
4. LLMs frequently forget or hallucinate this field → `role.assignment_brief` stays `NULL`
5. When `generate_assignment_for_role` runs later, it finds no saved brief → falls back to JD-only → random problems

**Fix:** `create_role_with_assignment` should look up the latest draft artifact for this conversation and auto-populate `brief` from `artifact.content.assignment.brief`. OR the runner should inject it when dispatching the confirmed tool call.

---

## Bug 4 — _prerender_for_confirm passes no brief/context (runner.py:295-302)

**File:** `backend/src/recruiter_agent/runner.py`, line 295-302

**Code:**
```python
brief = await gen_assignment(
    role_title=args.get("title") or "Role",
    jd_text=args.get("jd_text") or "",
    time_budget_hours=int(args.get("time_budget_hours") or 6),
    deadline_days=int(args.get("deadline_days") or 7),
    application_id=synthetic_id,
    candidate_id=synthetic_id,
    # NO user_brief, NO evaluation_spec, NO company_context, NO problem_count!
)
```

**Problem:** This function pre-generates the assignment for the confirm card UI. But it passes ONLY `title` and `jd_text`. No `user_brief`, `evaluation_spec`, `company_context`, or `problem_count`. The assignment preview shown to the recruiter therefore:
- Has no recruiter's brief (problems are JD-only → random)
- Has no evaluation dimensions to ground scoring
- Has no company context
- Defaults to 2 problems regardless of what was configured

**Fix:** Extract and pass all four missing parameters from the `args` dict (which contains the full draft content from `propose_role_draft`):

```python
draft_content = args.get("_draft_content", {}) or {}
brief = await gen_assignment(
    role_title=args.get("title") or "Role",
    jd_text=args.get("jd_text") or "",
    user_brief=(draft_content.get("assignment") or {}).get("brief"),
    evaluation_spec=args.get("evaluation_spec"),
    company_context=args.get("company_context"),
    problem_count=int(args.get("n_problems") or 2),
    ...
)
```

---

## Bug 5 — Legacy _maybe_generate_assignment has same issue (role_drafting.py:122-129)

**File:** `backend/src/api/role_drafting.py`, line 122-129

**Code:**
```python
brief = await gen_assignment(
    role_title=title,
    jd_text=jd_text,
    time_budget_hours=int(draft.get("assignment_deadline_days") or 6),
    deadline_days=int(draft.get("assignment_deadline_days") or 7),
    application_id=synthetic_id,
    candidate_id=synthetic_id,
    # NO user_brief, NO evaluation_spec, NO company_context, NO problem_count!
)
```

**Problem:** Same as Bug 4 but in the legacy `/agentic/roles/chat` endpoint. No brief/context passed, producing irrelevant assignment previews.

**Fix:** Extract `user_brief` from `draft.get("assignment", {}).get("brief")`, `evaluation_spec` from `draft.get("evaluation_spec")`, `company_context` from `draft.get("company_context")`, and `problem_count` from `draft.get("assignment", {}).get("n_problems", 2)`.

---

## Bug 6 — Assignment randomness when no brief exists (generators.py + assignment.py)

**File:** `backend/src/agent/generators.py` (gen_assignment)
**File:** `backend/src/agent/prompts/assignment.py`

**Problem:** When there is no `user_brief` and `evaluation_spec_json` is `"[]"`:
- The assignment prompt falls back to JD-only problem generation
- JD text alone is insufficient to produce role-specific, non-generic problems
- The hallucinated problems drift from what the recruiter actually wants

The assignment prompt (v12) has robust instructions for when brief IS present, but the fallback path (JD-only) produces vague problems.

**Fix:** At minimum, `evaluation_spec` and `company_context` should always be passed so the problem generator has role-specific criteria and company context to ground on, even without a brief.

---

## Summary of fixes needed

| # | File | What to fix |
|---|------|-------------|
| 1 | `jd_generation.py` | Add structured extraction block at top of prompt |
| 2 | `tools.py:_complete_draft_from_context` | Call `smart_defaults_for_role` → pass result as `partial` |
| 3 | `tools.py:create_role_with_assignment` | Auto-populate `brief` from draft artifact |
| 4 | `runner.py:_prerender_for_confirm` | Pass `user_brief`, `evaluation_spec`, `company_context`, `n_problems` |
| 5 | `role_drafting.py:_maybe_generate_assignment` | Forward brief/context from draft |
| 6 | Cross-cutting | Always pass `evaluation_spec` + `company_context` to `gen_assignment` |
