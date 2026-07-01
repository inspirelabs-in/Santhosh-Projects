# Prompt & Voice-Pipeline Audit

> Read-only audit. **No code was changed.** Every finding below was verified against
> source (file:line + verbatim quote). Subagent claims that did NOT survive
> verification are listed explicitly in the "Rejected claims" section so they don't
> get acted on by mistake.
>
> Date: 2026-06-26 · Branch: `jk/hr-agent-rewamp`

---

## PART A — PROMPT AUDIT

### The single root pattern
Every complaint is the **same failure mode**: the *correct* instruction exists, but a
**louder/more concrete competing instruction or a hardcoded default sitting next to it
out-weights it.** Fixing means *removing or demoting the competitor*, not adding more rules.

| Complaint | Correct rule | The louder competitor that wins |
|---|---|---|
| Pulse re-asks answered Qs | "Skip what's already answered" | "ask generously, do not ask just one or two" + 7-row skeleton |
| Assignment ignores brief | "recruiter brief is AUTHORITATIVE" | 50-line "READ THE JD FIRST" recipe + `problem_count=2` |
| Voice over-probes | "Only probe once" | "stay curious every turn" + per-question probe hints |

---

### A1 · Pulse recruiter agent

**A1.1 — "Ask generously" fights "skip answered"** · `recruiter_agent/prompts.py:45-58`
- L45: *"normally most of this set (5 to 7 questions) — **ask generously, do not ask just one or two**"*
- L56: *"Skip only what is already answered or truly unambiguous from context."*
- The skip rule is correct but buried; the generous-batch framing dominates, so when you
  give everything in one message it still re-asks. **No instruction to FIRST extract present
  fields, ask ONLY the missing ones, and skip `give_choice` entirely if nothing is missing.**

**A1.2 — `give_choice` SHAPE ships a 7-row skeleton** · `prompts.py:60-70`
- Literal placeholders (`"<level>"`, `"<band>"`, `"<org location>"`). A fully-formed 7-row
  template invites filling all 7 rather than pruning. Reinforces A1.1.

**A1.3 — location / landmark / remote conflated**
- Schema `models/artifacts.py:72-73`: only `location: str` + `remote_policy: str`. **No
  `landmark` field** — a landmark/area you type gets squashed into `location` or dropped.
- `prompts.py:65` offers location options `["<org location>", "<org location>", "Remote"]` —
  puts **"Remote" as a *location* value**, blending location with `remote_policy`. This is
  the "gave a place, got remote" bug.

**A1.4 — hardcoded location default "Hyderabad"** · `recruiter_agent/tools.py:913`
```python
"location": (similar or {}).get("location") or "Hyderabad",
```
- `smart_defaults_for_role` seeds "Hyderabad" when no similar role exists; it then feeds the
  give_choice options / draft. Contradicts the prompt's own L92 ("never quote a fixed number
  as if it were policy").

**A1.5 — draft is regenerated, not preserved**
- `propose_role_draft()` (prompts.py:82) is "NO arguments; the system reads the full
  conversation and writes the complete draft." That path runs the **`jd_generation`** prompt,
  whose instruction is to *rewrite* ("FULL JD, 300-450 words, VIBEY"). Nothing tells it to
  **preserve your verbatim wording**, and the transcript window is capped (~last 20 turns).
  → saved draft drifts from what you typed.

**A1.6 — three overlapping role-creation tools** · `recruiter_agent/schemas.py`
- `create_role`, `create_role_with_assignment`, `propose_role_draft` all "create a role";
  distinctions live in fine print. The system prompt never says *"always propose_role_draft
  first; never call create_role directly."* → inconsistent behavior across runs.

---

### A2 · Assignment generation · `agent/prompts/assignment.py` (v12) + `generators.py` + `tools.py`

**A2.1 — brief-authority drowned by the JD recipe**
- Brief marked authoritative early (`assignment.py:28-32`), then **50 lines of "READ THE JD
  FIRST" STEP 1-3** (`L44-88`) with a concrete decision tree ("React+Node → frontend feature").
  Even with the L46 skip-clause, the JD recipe is the longest/most concrete block, so the model
  treats it as the real task and the brief as intro context.

**A2.2 — problem count: default 2 shadows your "1"**
- `tools.py:344, 432, 644` all default `n_problems: int = 2`. Your "1" only sticks if the agent
  passes `n_problems=1`; otherwise `{problem_count}=2` sits in the prompt (`assignment.py:102`)
  and the model obeys the number. Brief count is inferred from prose → unreliable.

**A2.3 — padding can re-inflate to 2** · `tools.py:523-553`
- If the model honors "1" but `n=2`, padding synthesizes a 2nd problem; skipped only when
  `_existing_brief` is set → fragile (depends on brief being persisted before generation).

**A2.4 — hardcoded difficulty string** · `assignment.py:142`
- `difficulty: "Hard — 2.5 to 3 days"` emitted verbatim regardless of the brief.

---

### A3 · Voice prompts

**A3.1 — questions anchor to resume but don't probe gaps** · `llm/prompts/voice_screening.py:35`
- L35: *"Lead with 1-2 questions that reference a specific project/employer."* That's
  resume-*anchoring*, not candidate-*specific probing*. **Missing:** no fit-score breakdown /
  no instruction to target this candidate's weak dimensions or JD-vs-resume gaps. The fit-score
  gaps aren't even passed into the prompt vars (`activities/v1_voice_screening.py:156-159`).

**A3.2 — ElevenLabs agent over-probes** · `services/voice_prompts.py:187-204`
- L197-204 caps it correctly: *"probe ONCE … Only probe once … move on."*
- **But** L187-195 ("STAY CURIOUS … Connect to the next question conversationally … reference
  something they just said") encourages chattiness *every* turn, AND each question carries its
  own `probe-if-thin:` hint injected at `voice_provider.py:137-138`. There is **no global
  follow-up ceiling for the whole call** — only a per-thin-answer cap. A realtime agent reads
  this as license to keep digging.

**A3.3 — eval prompts are clean** ✅
- `screening_eval.py`, `voice_screen_eval_audio.py`, text-eval in `voice_screening.py`: all
  ground in `company_context_json` + `evaluation_spec_json`, score per-dimension, separate
  logistics from scoring, audio-eval ignores accent/stutter. No issues (matches your read).

---

### A4 · Cross-cutting prompt hygiene

| Issue | Location | Note |
|---|---|---|
| Hardcoded **"Aria"** name + **IST / 11 AM–8 PM** timezone | `voice_prompts.py` (several) | Not org/region-configurable |
| Hardcoded **"5+ years → leadership"** rule | `screening_gen.py:40` | Already `[TO_FIX]` in-file; should derive from seniority |
| **Dead prompts still maintained** | `agent/prompts/extract.py`, `agent/prompts/tailored_qs.py`, `llm/prompts/interview_report.py` | Superseded; archive/remove |
| Legacy silent defaults | `role_drafting.py:99-100` | Legacy turn-by-turn path |

---

## PART B — VOICE PIPELINE BUG HUNT (qns-gen → call → webhook → eval → advance)

> I traced every stage and **verified each claim in source.** A subagent produced ~22 candidate
> bugs; **most were speculation and are rejected below.** The verified list is short.

### B0 · What is CLEAN (verified) ✅
- **Evaluator** (`activities/v1_evaluate_voice_call.py`): scoring, score-based routing via
  `advance_candidate`→`route_score`, fact backfill, evidence/decision recording — all sound.
  Fact backfill **does** persist (mutates ORM objects inside the `session_scope()` opened at
  L47, which commits on exit).
- **Idempotency** (`db/repositories/voice_call.py:231-250` `claim_processing`): atomic DB CAS —
  `UPDATE … WHERE processing_status IN (pending, failed)`, returns `rowcount>0`. Exactly one
  concurrent caller wins. Correct.
- **Quality guards**: blank-transcript gate (`v1_evaluate_voice_call.py:103-119`) and voicemail
  classifier (`:175-209`) both park to `NEEDS_HR_REVIEW` instead of scoring a dropped call. Good.
- **Primary question-gen** (`activities/v1_voice_screening.py`): includes
  `**scoring_prompt_vars(...)` → questions are role-grounded.

### B1 · VERIFIED issues

**B1.1 [MED] — fallback question-gen drops role grounding** · `activities/v1_dispatch_voice_call.py:234-253`
- Explicit `[TO_FIX] SM-5` comment confirms it: the fallback omits
  `**scoring_prompt_vars(role.evaluation_spec, role.company_context)`, so
  `{company_context_json}` / `{evaluation_spec_json}` **leak into the prompt as literal text**
  and questions lose role grounding.
- **Blast radius:** the `dispatch_voice_call` fallback path only (non-primary). Primary
  screening path is fine. Scores from this path are not comparable to the primary path.

**B1.2 [MED] — phone not normalized before dispatch** · `voice_provider.py:220`, `v1_voice_screening.py:212`
- `to_number: spec.candidate_phone` is passed **raw**. A deterministic normaliser exists
  (`services/dedup.py:27 normalise_phone()`, `phonenumbers` lib) and resume-parse *asks* the LLM
  for E.164 — but the voice path never runs the deterministic normaliser on `candidate.phone`.
- **Failure:** a stored number with spaces/brackets/missing `+CC` (LLM didn't normalize, or
  phone came from a non-resume source) → ElevenLabs rejects / mis-dials → NO_ANSWER + retry loop.
- **Fix direction:** call `normalise_phone()` on `candidate.phone` right before building `VoiceCallSpec`.

**B1.3 [MED] — over-probing has no global ceiling** (same as A3.2)
- Prompt-level; per-thin-answer cap exists, whole-call cap does not. Plus per-question
  `probe-if-thin` hints injected at `voice_provider.py:137-138`.

**B1.4 [LOW/MED] — confirmation reschedule always defaults round="technical"** · `webhooks_voice.py:888-889`
```python
meta = (voice.questions or {}) if isinstance(voice.questions, dict) else {}
round_name = meta.get("round") or "technical"
```
- For confirmation calls `voice.questions` is `None` → `{}` → round defaults to `"technical"`.
  A reschedule of a CEO/HR-round confirmation would re-schedule as `technical`.
- **Caveat:** this is the *meeting-confirmation* path, not the screening pipeline. Needs a check
  on whether the round is recoverable elsewhere (meeting_session); flagged, not yet confirmed harmful.

### B2 · OPERATIONAL (not a code bug, but can silently break calls)

**B2.1 — ElevenLabs "Security > Override" toggles** · `voice_provider.py:124-131`
- Per-call prompt/first_message overrides are **silently ignored** (no 400) unless the agent's
  Override toggles for prompt + first_message are enabled in the ElevenLabs dashboard. If those
  are off, calls use dashboard defaults / go silent. Verify these are ON for the tenant.

### B3 · REJECTED subagent claims (do NOT act on these — verified false / by-design)

| Claimed "bug" | Verdict | Why |
|---|---|---|
| CRITICAL idempotency CAS race (`webhooks_voice.py:841`) | **FALSE** | `claim_processing` is an atomic DB CAS in the WHERE clause; app-level check timing is irrelevant |
| Gemini audio-eval exception swallowed (`v1_evaluate_voice_call.py:225`) | **FALSE** | No try/except there; an exception propagates. None-fallback is by design |
| Extracted facts never flushed (`:293/:392`) | **FALSE** | Mutates ORM objects inside the `session_scope()` that commits on exit |
| Score recompute overwrites explicit verdict (`:274`) | **By design** | Routing is score-based, not verdict-based; recompute is intentional & documented |
| `VOICE_SCREEN_EVALUATED` written unconditionally (`:297`) | **By design** | Documented legacy back-compat dual-write; `advance_candidate` sets the V2 cursor |
| Non-atomic reject flow (`stage_runner.py:198`) | **Not a clear bug** | Multi-session by nature; errors are logged; no evidence of orphaning in normal flow |
| Missing `Any` import (`v1_voice_screening.py:85`) | **FALSE/trivial** | `from __future__ import annotations` makes it a string annotation; no runtime impact |

---

## PART C — Recommended fix order (when you give the go-ahead)

**Highest leverage (matches your 3 complaints):**
1. **A1.1 + A1.2** — Pulse: replace the "ask generously" framing + 7-row skeleton with
   *extract-present-fields-first, ask-only-missing, skip give_choice if nothing missing.*
2. **A2.1 + A2.2** — Assignment: gate/strip the JD recipe when a brief exists; infer count
   from the brief and stop defaulting `n_problems` back to 2.
3. **A3.2 / B1.3** — Voice: add a hard global follow-up ceiling; soften the "stay curious
   every turn" copy.

**Correctness / data fidelity:**
4. **A1.3** — add a `landmark` field; separate `location` from `remote_policy` in options.
5. **A1.5** — make `propose_role_draft` preserve verbatim recruiter input.
6. **B1.2** — run `normalise_phone()` before voice dispatch.
7. **B1.1** — add `scoring_prompt_vars` to the fallback question-gen (SM-5).

**Hygiene:**
8. **A1.4 / A4** — remove hardcoded "Hyderabad", externalize "Aria"/IST, archive dead prompts.
9. **B2.1** — confirm ElevenLabs Override toggles are enabled (deploy checklist).
