# Pulse / HR-Agent — Bug Report + Fix Plan

Investigation date: 2026-06-24. Scope: chat, `/candidates`, `/roles` — full-stack root-cause trace (FE → API → backend → DB). This revision adds the **agreed target behavior** and **end-to-end file lists** for each fix.

Severity: 🔴 critical · 🟠 major · 🟡 ux.

---

## Status legend (updated 2026-06-26)

`[DONE]` fixed end-to-end (compiles + typechecks; not yet runtime-tested) · `[PARTIAL]` partly fixed, remainder noted · `[TO_FIX]` deliberately left (out of scope this pass), marked `[TO_FIX]` in code · `[WONTFIX]` no longer wanted.

| Item | Status | Item | Status |
|------|--------|------|--------|
| SM-1 | [DONE] | FE-1 | [DONE] |
| SM-2 | [DONE] (minimal: KeyError guard; analysis already cursor-first) | FE-2 | [DONE] (generic needs_review path + relabel; legacy branches left as fallbacks) |
| SM-3 | [TO_FIX] (screening) | FE-3 | [DONE] |
| SM-4 | [DONE] | FE-4 | [DONE] |
| SM-5 | [TO_FIX] (voice fallback) | FE-5 | [DONE] |
| SM-6 | [TO_FIX] (voice fallback) | FE-6 | [DONE] |
| SM-7 | [DONE] (docstring + dead-code marker) | FE-7 | [DONE] (read-only view; editor needs a BE endpoint) |
| SM-8 | [TO_FIX] (route_score) | Bug 1 | [DONE] (upload-your-own skipped) |
| SM-9 | [TO_FIX] (legacy endpoints, no FE caller) | Bug 2 | [DONE] |
| SM-10 | [TO_FIX] (dead prompts) | Bug 3 | [DONE] |
| Bug 4 | [DONE] | Bug 5 | [WONTFIX] (voice is mandated) |
| Bug 6 | [DONE] | Bug 8 | [TO_FIX] (quick_replies works) |
| Prompts F: assignment / ceo_brief / journey CTC / voice_eval_audio | [DONE] | Prompts F: screening / verdict-vocab / emotion / hindi | [TO_FIX] |

Note: "Management Round" was implemented **label-only** (stage_key stays `"ceo"`); a full key rename + data migration was deliberately not done. Prompt VERSIONs were bumped — run `backend/scripts/upload_prompts_to_langfuse.py` to push them.

---

# Cluster D — V2 Stage Management (QC, 2026-06-25)

Full FE→engine→DB trace of the V2 pipeline (`pipeline_engine` planner, `stage_runner`/`auto_progress` runner, the 6 decision points, the `stage_results` overlay, every scoring prompt's variable wiring). The core engine is sound; the bugs below are the leaks around it. Lead bug (SM-1) is the one in the candidate-detail screenshot.

### 🔴 SM-1 [DONE] — "Proceed to Next Round" sends a V2 `stage_key` to the legacy enum endpoint → `invalid_stage`, and it shows while the current stage is still `on_going`

**Symptom (screenshot):** stepper shows *Initial Voice Screen — on_going*, but below it a "Proceed to Technical Interview" card appears and clicking it returns `invalid_stage`.

**Root cause #1 (vocabulary mismatch — the 400):** `ProceedToNextRound` derives the target from `stage_view`: `targetStage = nextEnabled.stage_key` (a **V2 key** like `"technical"`) — `frontend/src/app/(app)/candidates/[id]/page.tsx:1319-1334`. It then POSTs that to the **legacy** endpoint `POST /dashboard/v1/candidates/{id}/stage` (`:1361`) = `set_stage_action` (`backend/src/api/v1_dashboard.py:1330`), which does `PipelineStage(body.stage)` (`:1337`). The legacy `PipelineStage` enum (`backend/src/models/v1.py:24-61`) has **no** `technical`/`voice_screen`/`fit`/`assignment`/`intake`/`parse` members — only `technical_meeting_scheduled`, `technical_evaluated`, … So every interview/fit/voice/assignment key raises `ValueError` → `invalid_stage` (`:1339`). (The few V2 keys that *collide* with legacy values — `decision`, `offer`, `report_ready`, `rejected`, `hired` — happen to validate; everything else 400s. The hardcoded fallback map at `:1338-1346` uses legacy values that validate, but the dynamic path runs whenever `stage_view` exists, i.e. always.)

**Root cause #2 (no completion gate):** the panel renders whenever *any* next enabled stage exists (`:1324, 1354`); it never checks the current stage is actually complete (verdict `pass` / `processing_status=processed`). So at `voice_screen`/`on_going` it already offers "Proceed to Technical Interview" — contradicting the stepper and letting a recruiter skip the in-progress screen **and its evaluation**.

**Fix (end to end):**
- Add a V2 advance endpoint, e.g. `POST /dashboard/v1/candidates/{id}/advance`, that calls `advance_candidate(application_id, completed_stage_key=app.current_stage_key, verdict=StageVerdict.PASS)` — mirrors `resolve-review` (`v1_dashboard.py:1544`) and `_advance_from_gate` (`api/meetings.py:520`). This goes through the engine: it advances the cursor and fires/parks the correct next stage. Repoint `handleProceed` (`page.tsx:1361`) at it; stop sending a target stage at all.
- Gate the panel: only show when the current stage's verdict is `pass`/`processed` **or** it is a human gate awaiting a decision. While a stage is `on_going`, hide it (or, if an explicit override is desired, label it "Skip & advance" and confirm).
- Delete the hardcoded `ROUND_TRANSITIONS` map (`:1337-1348`) once advance is engine-driven.

### 🟠 SM-2 [DONE] — Interview rounds (tech vs CEO/manager) are differentiated by a hardcoded round name, not the `stage_key`

In V2, each interview round is a separate `role_pipeline_stages` row with a distinct `stage_key` + `label` but the same `stage_type="interview"`. The `stage_view` stepper (`components/candidate-detail/pipeline-stepper`) renders them correctly by label. **But booking/advance/analysis still differentiate by a conventional `round ∈ {technical, ceo, hr}` mapped to a stage_key of the same literal name:** `api/meetings.py:140` (`current_stage_key = body.round`), `_ROUND_TO_STAGE_KEY` (`meetings.py:544`), `_round_to_stage_key` (`v1_meeting_analysis.py:46`), and the FE fallback map (`page.tsx:1342-1346`). Consequences: a renamed interview stage, a **second** technical round, or a "manager" round that isn't literally `ceo` can't be addressed — booking writes a `current_stage_key` that isn't a real stage, and after analysis `plan_transition` hits the `start=-1` fallback (`pipeline_engine.py:182-190`) and **re-fires from the top of the pipeline**. (The legacy `components/pipeline-stepper.tsx` — 8 hardcoded steps incl. fixed CEO/HR — is also still in the tree; superseded by the stage_view stepper but hardcodes the GrabOn chain if rendered anywhere.)

**Fix:** differentiate by `stage_key` end to end. Booking targets the specific next `interview` `stage_key` taken from the cursor/`stage_view` (the meeting `round` becomes a display label only). Keep `v1_meeting_analysis`'s existing preference for `current_stage_key` and drop the round→key fallback. Feed the analysis prompt the stage `label` (e.g. "Manager Round") for tone instead of the fixed `round`.

### 🔴 SM-3 [TO_FIX] — The `screening` stage type is wired to nothing

`auto_progress._fire_screening` (`backend/src/services/auto_progress.py:453`) enqueues `run_apply_to_screening_stage` — **no such Arq job** in `workers/jobs.py` — and the inline fallback imports `dispatch_screening_stage` from `v1_generate_screening`, which **doesn't exist** (only `generate_screening_questions`). A role that adds a `screening` stage therefore either fires a job no worker handles (arq on) or `ImportError`s → parks at `NEEDS_HR_REVIEW` (arq off). The generic-pipeline promise fails for this stage type. (Default pipeline uses voice as the screen, so unhit today.)
**Fix:** wire a real screening dispatcher (generate + email the questionnaire, advance via `advance_candidate`), or reject a `screening` stage at JD-config time instead of stalling at runtime.

### 🟠 SM-4 [DONE] — `run_assignment_processing` post-block runs even after an auto-reject

`backend/src/pipeline/v1.py:500-518`: after `complete_assignment_submission()`, the code **unconditionally** sets legacy `current_stage = REPORT_READY`, sends the candidate an "under final review" notification, and emails the tech panel. If the assignment stage is `mode=auto` and the submission scored in the FAIL band, the runner already auto-rejected + emailed the rejection — then this block tells the candidate they're "under review", asks the tech panel to review a rejected candidate, and flips the legacy stage off `rejected`.
**Fix:** capture the return of `complete_assignment_submission` and skip the post-block when it starts with `rejected:`/`parked_fail`. (Default assignment stage is `manual`, so FAIL parks instead of auto-rejecting — why it hasn't bitten yet.)

### 🟠 SM-5 [TO_FIX] — Voice question-gen fallback path drops role grounding (orphaned placeholders)

`_generate_or_reuse_questions` (`backend/src/activities/v1_dispatch_voice_call.py:234`) calls `voice_screen_gen` **without** `scoring_prompt_vars(...)`, while the primary generator (`v1_voice_screening.py:145`) includes it. The `VOICE_SCREEN_GEN_V1` prompt references `{company_context_json}` and `{evaluation_spec_json}`; `compile_prompt` leaves unmatched placeholders as **literal text** (the same failure mode as the old `{user_brief_override}`). So when this fallback generates questions, the literal tokens leak into the prompt and the questions lose all role-specific grounding.
**Fix:** add `**scoring_prompt_vars(role.evaluation_spec, role.company_context)` at `v1_dispatch_voice_call.py:247`.

### 🟡 SM-6 [TO_FIX] — Text voice-eval prompt has an orphaned `{paralinguistic_json}`

`VOICE_SCREEN_EVAL_V1` (text fallback) references `{paralinguistic_json}` (`llm/prompts/voice_screening.py:93`) but the call site `v1_evaluate_voice_call.py:245` never supplies it → literal `{paralinguistic_json}` reaches the LLM. Low impact (prompt says "ignore if absent") and it's the fallback path (audio eval is primary), but it should be supplied or removed.

### 🟡 SM-7 [DONE] — `assessment_review` stage type is silently skipped

The deprecated `assessment_review` type has no branch in `pipeline_engine._action_for` (not in `_FIRE_ACTIONS`/`_GATE_ACTIONS`/`_INLINE_TYPES`) → returns `None` → the engine walks past it without parking. `StageAction.PARK_REVIEW` and `_GATE_EVENT[PARK_REVIEW]` are now dead, and the `pipeline_engine` module docstring still describes `assessment_review` as a manual gate. A legacy role relying on a real review gate would skip human review.
**Fix:** either map `assessment_review` → an explicit park, or hard-fail/log at config time; update the stale docstring.

### 🟡 SM-8 [TO_FIX] — `route_score` clobbers a per-stage threshold equal to the default constant

`backend/src/services/evaluation.py:49-56`: if a caller passes a per-stage `threshold` equal to `EVAL_PASS_THRESHOLD`, the `if threshold == EVAL_PASS_THRESHOLD:` branch overwrites it with `settings.eval_pass_threshold`. A role whose stage explicitly sets `pass_threshold` to the default number silently gets the env value. Harmless when env == constant.
**Fix:** use a `None` sentinel to detect "caller did not override" instead of value-equality.

### 🟡 SM-9 [TO_FIX] — Legacy reject endpoints bypass the V2 runner (and are now orphaned)

The `reject` branches of `tech_decision`/`ceo_decision`/`hr_decision` (`backend/src/api/v1_dashboard.py:1421/1590/1732`) reject via legacy `set_stage(REJECTED)` + a direct email, **not** `advance_candidate(FAIL)` — leaving `current_stage_key` stale and `Application.status` unset through the runner. **However, the FE no longer calls these** (the approval surface uses `/resolve-review` + `/agentic/*` → `_advance_from_gate` → `stage_runner`). So this is now a **stale-endpoint** issue, not a live bug → see `stale.md` §A4. If anything still hits them, route reject through `advance_candidate(verdict=FAIL)`; otherwise stale + remove.

### ℹ️ SM-10 [TO_FIX] — Dead prompts (heads-up, not a runtime bug)

`interview_report` (`activities/report.py`) and `score_open_text` (`activities/score_responses.py`) are both on `# [SCRAPE]`-marked dead activities — not on the live V2 path (live post-interview synthesis is `v1_meeting_analysis` + `v1_journey_report`). Edits to those two prompt files are inert at runtime; either revive a caller or stop maintaining them.

**What's solid (verified):** `plan_transition` is pure and correct (handles 1-stage / no-CEO / reorder / unknown-key); single V2 cursor ownership is honored (the `set_stage` dual-write is correctly commented off); `route_score` centralizes verdicts (None → needs_review, never auto-rejects a missing score) with per-stage threshold override; `complete_assignment_submission` correctly splits scored vs no-score; the `stage_results` overlay is derive-don't-materialize with a CAS idempotency guard. Prompt variables are in sync for fit / screening / audio-voice-eval / assignment / meeting / primary voice-gen.

**Suggested order:** SM-1 (the visible blocker) → SM-3, SM-4 (runtime impact) → SM-2 (the generic-interview promise) → SM-5 → SM-6/7/8/9 → SM-10.

---

# Cluster E — Frontend (V2 pipeline rendering, 2026-06-25)

The FE still renders large parts of the candidate surface off the **legacy `current_stage` enum** and **field-presence**, instead of the V2 `stage_view` (the role's real stages + per-stage verdict/status). This is the "random showing of things." Pair each FE fix with its BE counterpart so nothing breaks (see migration strategy at top).

### 🔴 FE-1 [DONE] — `ProceedToNextRound` (sibling of SM-1)
Covered by SM-1. FE root cause: `frontend/src/app/(app)/candidates/[id]/page.tsx:1319-1361` sends a V2 `stage_key` to the legacy `/v1/.../stage` endpoint and never gates on the current stage's verdict. Fix in SM-1.

### 🔴 FE-2 [DONE] — Admin review / approval panel keys off the legacy enum, not `stage_view`
`frontend/src/components/admin-review-panel.tsx`: most action branches are selected by string-matching the legacy `currentStage` (`needs_hr_review`, `assessment_pending_review`, `technical_pending_approval`, `ceo_pending_approval`, `hr_evaluated`) and post to fixed V1-shaped endpoints. For a V2 role whose `current_stage_key` is role-defined (e.g. `technical`, `manager_round`), those branches don't match → the panel falls through to `return null` and the recruiter gets **no approve/reject control** for interview rounds. The one correct branch is the `needs_review` + `parked` case (`:80-113`, posts `/resolve-review`) — that's the model the rest should follow.
**Fix:** drive every panel off `stage_view` (`stage_type === "interview"|"decision"` + `verdict`/`processing_status`) and post to a generic resolve/advance endpoint keyed on `stage_key`.

### 🟠 FE-3 [DONE] — Sections render off field-presence, not stage ("random showing")
- **Assignment** (`candidates/[id]/page.tsx:1584-1588`): gate is `legacyStage.startsWith(...) || data.assignment_submission` — any stray submission renders the panel; the stage check uses hardcoded legacy prefixes (`assessment`, `tech_interview`) instead of a `stage_view` entry with `stage_type === "assignment"`.
- **Fit / Screening / Voice** (`:1525-1554` score cards, `:1575-1581` sections): render purely on presence of `data.fit_breakdown` / `screening_evaluation` / `voice_evaluation`. A role without that stage (or a stale payload) still surfaces the card.
**Fix:** gate each section on a reached `stage_view` entry of the matching `stage_type`.

### 🟠 FE-4 [DONE] — Candidates list, kanban, and quick-view render the legacy enum, ignoring `current_stage_key`/`stage_view`
`candidates/page.tsx:430` (`StatusTag stage={c.current_stage}`), `components/candidates/kanban-view.tsx:21-62` (columns from the fixed V1 `STAGE_ORDER` + `c.current_stage`), `components/candidates/quick-view-panel.tsx:78`. The list interface even declares `current_stage_key` (`page.tsx:45`) but never uses it. A V2 candidate on a custom `stage_key` falls into a stray underscore-label kanban column (`kanban-view.tsx:56-59`) and `StatusTag` falls back to `"applied"` for unknown stages (`components/status-tag.tsx:162`).
**Fix:** drive `StatusTag` label/color and kanban columns from `current_stage_key` + the role's `stage_view` labels, not the frozen enum. Also fix `STAGE_ORDER` (`status-tag.tsx:89-108`) which omits many real stages — or generate it from the backend taxonomy.

### 🟠 FE-5 [DONE] — Meeting reports hardcoded to technical/ceo/hr
`candidates/[id]/page.tsx:837-873` (`roundLabels = {technical, ceo, hr}`) and `admin-review-panel.tsx:44-50` (`meetingReports: {technical?, ceo?, hr?}` looked up at `:203/231/259`). A renamed interview round, a 2nd technical round, or a `manager_round` can never surface its report.
**Fix:** key meeting reports by `stage_key`; resolve labels from `stage_view[].label`. (FE half of SM-2.)

### 🟡 FE-6 [DONE] — Live stepper: dead `processing` branch + missing `on_going` verdict
`components/candidate-detail/pipeline-stepper.tsx`: `isCurrent = s.is_current || processing_status === "processing"` (`:21`) makes the dedicated `processing` branch (`:44`) unreachable (a background-processing non-current stage shows the active spinner). And there's no `on_going` verdict case (`:17-52`) — an `on_going` stage renders grey "pending" with a literal `on_going` caption (`:79-91`).
**Fix:** order the `processing` check before `isCurrent`; add an `on_going` case with active styling + a proper label.

### 🟡 FE-7 [DONE — read-only] — Role page has no pipeline-stages editor
`frontend/src/app/(app)/roles/[id]/page.tsx`: shows JD/assignment/eval_spec/company_context and a single read-only `screening_modality` (`:646-656`), but **no UI for `role_pipeline_stages`** — the ordered stage list the whole V2 model runs on. Recruiters can't see or change which stages a role runs or their auto/manual mode.
**Fix:** add an ordered pipeline-stages editor (stage_type + label + mode + enable).

---

# Cluster F — Prompts: static enums that must be passed dynamically (2026-06-25)

Principle: a generic per-role pipeline means **fixed taxonomies baked into prompt TEXT are scalability bugs** when the real value is role-configurable. `compile_prompt` leaves an unmatched `{placeholder}` as **literal text** (silent), so an un-supplied var is a leak. Keep only output-schema keys the parser depends on.

**Highest priority (active prompts):**
- 🟠 **[DONE] `llm/prompts/voice_screen_eval_audio.py`** — hardcoded **score bands** (`:66-71`, `85-100/65-84/…`) and a fixed **70/30 content/audio weighting** (`:75`); thresholds/weights belong in code (`route_score` / per-stage config), not the prompt. Also a baked **"builder/ownership/proof-of-work" culture lens** (`:13`) + ownership-vs-passive word lists (`:43-54`) that bias against manager/coordinator roles — should come from `company_context`/`evaluation_spec`.
- 🟠 **[DONE] `agent/prompts/assignment.py`** — fixed **technical vs non-technical competency dimension lists** (`:146-149`) and a forced **exactly-5-dims-×-20** weighting (`:139-144`); should derive from the role's `evaluation_spec.dimensions`. Also `Problem count: 2` baked (`:123`).
- 🟠 **[TO_FIX] `screening_gen.py:37-38`** — hardcoded **"5+ years" seniority threshold** + canned senior/junior question themes; derive from the role's seniority/spec.
- 🟠 **[PARTIAL: CTC threshold DONE; screening_verdict TO_FIX] `journey_report.py:114` + `v1_journey_report.py:83`** — `screening_verdict` reads a `verdict` key the **score-only** screening eval no longer emits → always `"n/a"` (stale). Route the score to a label instead. Also `:142` hardcoded **20% CTC** threshold.
- 🟠 **[DONE] `ceo_brief.py:28`** — hardcoded **"technical interview"** round name (use the stage label); the prompt also never receives `company_context_json` (inconsistent with the scoring prompts).

**Flag for owner (fixed axes parallel to the dynamic `evaluation_spec`):** [TO_FIX] (includes the screening axes; out of scope this pass)
- `screening_eval.py:37-44` and `voice_screening.py:96-99` — three fixed scoring axes baked alongside the role's dynamic dimensions.
- Fixed verdict vocabularies in `interview_report.py:45`, `journey_report.py:74`, `ceo_brief.py:37` (display-only/markdown — confirm vs retire); fixed emotion buckets `meeting_analysis.py:86`.
- `classify_email.py:34` `hindi_content` flag — region-specific; make generic/config-driven.

**Legitimately fixed (KEEP — parser/output-schema contracts):** `fit_score.py` (already the clean model — v13), `parse_resume.py`, `rejection_message.py`, the Pulse `RECRUITER_SYSTEM` (pipeline-template/SYSTEM-DEFAULTS tables + canned questions already removed), `jd_generation.py` (`pipeline_stage_types` correctly driven from the `StageType` enum). **No live orphaned `{placeholder}`s** except the dead files and the two wiring leaks already filed as SM-5 (`{company_context_json}`/`{evaluation_spec_json}` in the voice-gen fallback) and SM-6 (`{paralinguistic_json}` in text voice-eval).

---

# Migration strategy — fixing FE+BE without breaking V1

The whole theme: **existence + display + progression must follow the role's `stage_view` (V2), not the legacy `current_stage` enum or raw field-presence.** Do it expand→cutover→stale, never rip-and-replace:

1. **One V2 advance/resolve endpoint** keyed on `stage_key` (wraps `advance_candidate`) — fixes SM-1/FE-1/FE-2 at the source. Keep `/stage` (legacy enum) working for any old caller until removed.
2. **FE reads `stage_view`** for every stage-derived render (sections, badges, kanban, stepper, meeting reports, proceed/approve gates). The API already returns `stage_view` and `current_stage_key`; the FE just stops reading `current_stage`. Legacy fields stay on the payload as a projection (no BE break).
3. **Prompts**: pass role-config values (stage label, eval_spec dimensions, seniority) as variables; pull thresholds/bands/weights out of prompt text into code. Bump each prompt's VERSION + sync Langfuse.
4. **Mark V1 stale** (see `stale.md`) only after its V2 replacement is the sole live path — comment, don't delete, this pass. Deletion + drop-migrations are a separate, test-gated change.

This keeps V1 as a passive projection while V2 becomes authoritative on both sides, so no single change breaks the running app.

---

## Target assignment lifecycle (the model all assignment fixes build toward)

The assignment is **role-level** (`Role.assignment_brief` text + `Role.assignment_problem_doc_key` PDF). The agreed lifecycle:

1. **Assignment only exists if an `assignment` stage is selected in the role's pipeline.** No assignment stage → never create, generate, gate, or display an assignment. Nothing.
2. **Once the JD is done, Pulse asks about the assignment** (it never auto-generates). Two branches:
   - **Recruiter has their own** → they **upload** it (PDF/DOCX). It's stored as the assignment doc (and parsed to `assignment_brief` text); used as-is, never overwritten.
   - **Recruiter has none** → Pulse **drafts** it via a **separate, explicit tool call** (`generate_assignment_for_role`, distinct from `propose_role_draft`), using the role's JD as context. The result is **editable TEXT** the recruiter can revise.
   The assignment brief is visible + editable in both the chat panel and the role page.
3. The recruiter edits the text, then **"posts"** it → backend renders the brief to a **PDF** (reuse `render_assignment_pdf`) and stores `assignment_problem_doc_key`. (An uploaded file is already the PDF and skips this render step.)
4. **The role goes live (`status="open"`) only once the assignment PDF exists** — when an assignment stage is selected. Until the PDF is posted, the role is held in a non-live state (introduce `RoleStatus.DRAFT = "draft"`), so it is not on the careers list and accepts no applicants. (If no assignment stage is selected, the role goes live immediately.)
5. **Editing the role re-runs the same loop**: changing the brief text invalidates the PDF (clear `assignment_problem_doc_key`, drop status back to `draft`); re-posting renders a fresh PDF and flips back to `open`.

This single model resolves Bugs 1–3 together: existence is gated on real intent (the stage), the brief is a first-class editable text, the PDF is an explicit step, and "active" is gated on the PDF.

**Infra that already exists (reuse, don't rebuild):** `render_assignment_pdf` (`backend/src/services/assignment_pdf.py:180`); `_persist_assignment(save=True)` render+upload+set-doc-key (`backend/src/recruiter_agent/tools.py:506-557`); manual PDF upload endpoint (`backend/src/api/roles.py:541-542`) and download (`roles.py:562-565`).

---

## Cluster A — Assignment lifecycle

### Where it lives (shared context)
- `Role.assignment_brief` (text), `Role.assignment_instructions`, `Role.assignment_deadline_days`, `Role.assignment_problem_doc_key` (PDF), `Role.assignment_problem_filename` — `backend/src/db/base.py:99-103`.
- Candidate submission is separate: `Application.assignment_submission` — `db/base.py:148`.
- Pre-creation it rides in the draft artifact as `RoleDraftAssignment` (`enabled, brief, instructions, n_problems, time_budget_hours, deadline_days`) — `backend/src/models/artifacts.py:36-59`.
- Role status enum is `open/paused/filled` — `backend/src/models/candidate.py:70-73`; default `open` — `db/base.py:91`.

---

### 🔴 Bug 1 [DONE] — Pulse can't read or edit the assignment, and never asks (it silently generates)

**Root cause (FE + BE):**
- The draft `ArtifactPanel` TS type only models `enabled / n_problems / time_budget_hours / deadline_days` — **no `brief`, no `instructions`** (`frontend/src/components/recruiter-chat/ArtifactPanel.tsx:65-71`); the rendered assignment section shows only numeric inputs + a checkbox (`ArtifactPanel.tsx:678-752`). The brief text is invisible/uneditable in chat.
- Pulse has WRITE tools (`set_role_assignment_brief` `tools.py:1195-1212`, `generate_assignment_for_role` `tools.py:408`) but **no read tool returns the brief** — `list_roles` returns title/status/ctc/location only (`tools.py:189-224`, `schemas.py:46`), `get_candidate` returns only the submission. Pulse is blind to current assignment content, so it can only blind-overwrite, never targeted-edit.
- Generation is silent (Bug 2), so Pulse "isn't asking."

**Target behavior:** after the JD is done, Pulse asks about the assignment; the recruiter either uploads their own or asks Pulse to draft one (separate tool call, JD-grounded). Pulse can read the brief + PDF state and edit the brief text. The chat panel shows the brief as an editable textarea (only when an assignment stage is selected).

**Fix — files to touch end to end:**
- `frontend/src/components/recruiter-chat/ArtifactPanel.tsx` — add `brief: string` and `instructions: string` to the `Draft.assignment` type (`:65-71`); render a brief **textarea** + instructions field in the assignment section (`:678-752`), shown only when the pipeline contains an `assignment` stage; wire both through `patch(...)`. Add an **upload control** ("upload your own take-home") for the recruiter's-own branch.
- `backend/src/recruiter_agent/schemas.py` — add a `get_role` tool schema (returns title, status, pipeline stages, `assignment_brief`, `has_problem_doc`, deadline).
- `backend/src/recruiter_agent/tools.py` — implement `get_role` (read brief + doc state); ensure `set_role_assignment_brief` (`:1195-1212`) on edit **clears `assignment_problem_doc_key`** and resets status to `draft` (re-post required); register `get_role` in the tool registry. `generate_assignment_for_role` (`:408`) stays the **separate** assignment-draft tool (already JD-grounded via `role.jd_text`, `:453`).
- `backend/src/api/roles.py` — the existing upload endpoint (`:541-542`) is the recruiter's-own branch; on upload, parse the file to `assignment_brief` text (reuse the `parse_attachment`/parser path) and flip status `draft → open`.
- `backend/src/recruiter_agent/prompts.py` (`RECRUITER_SYSTEM_V1`, not Langfuse-managed) — instruct: **once the JD is done and an assignment stage is selected, ask** "do you have your own take-home (upload it), or should I draft one?"; on "draft" call `generate_assignment_for_role` (separate call, never folded into `propose_role_draft`); read current state via `get_role`; after edits, prompt to "post" to produce the PDF. Never generate unasked.

---

### 🔴 Bug 2 [DONE] — An assignment is generated even when none was requested

**Root cause (BE):** assignment defaults `enabled=True` everywhere and apply auto-generates whenever `enabled and brief == ""`:
- `RoleDraftAssignment.enabled: bool = True` (`backend/src/models/artifacts.py:47`).
- Draft template hardcodes `"assignment": {"enabled": true, "brief": ""}` (`tools.py:1812`).
- Apply path generates when `enabled and not brief` (`backend/src/api/recruiter_chat.py:408-417`).
- `create_role` path also auto-generates whenever the seeded pipeline has an assignment stage (`tools.py:1139-1145`), and the default pipeline always includes one (`pipeline.py:134`).
- The existing skip-gate (`ensure_role_assignment` `tools.py:597-604`) only skips on a company-provided brief — it never checks *intent*.

**Target behavior:** generation happens **only** when (a) an `assignment` stage is selected, (b) the recruiter has no upload of their own, and (c) they explicitly ask Pulse to draft — via the separate `generate_assignment_for_role` tool, never as a side-effect of role creation. Default is no generation. "Active" is gated on the PDF (uploaded or posted), not on auto-filled text.

**Fix — files to touch end to end:**
- `backend/src/api/recruiter_chat.py` (`apply_artifact`) —
  - Remove the auto-generation block (`:408-417`).
  - Compute `has_assignment_stage` from `draft.pipeline` (any stage with `stage_key/stage_type == "assignment"`).
  - Set role `status`: `"open"` if no assignment stage; `"draft"` if an assignment stage exists but no PDF yet (`:358`).
  - Keep persisting a company-provided brief as text (`:363-368`) but do **not** render a PDF or open the role until the recruiter posts.
- `backend/src/recruiter_agent/tools.py` (`create_role`) — remove the unconditional `ensure_role_assignment` call (`:1139-1145`); gate identically (assignment stage + explicit request only). Keep `ensure_role_assignment`/`generate_assignment_for_role` as the explicit, asked-for path.
- `backend/src/models/artifacts.py` — change `RoleDraftAssignment.enabled` default to `False` (`:47`); let it be set True only when an assignment stage is chosen.
- `backend/src/recruiter_agent/tools.py` (draft template, `:1812`) + `backend/src/llm/prompts/role_drafting.py` — stop hardcoding `enabled: true`; derive from whether an assignment stage is in the drafted pipeline; keep `brief: ""` unless the manager described one.
- New **"post assignment"** path (the text→PDF→active step): add an endpoint (e.g. `POST /dashboard/roles/{id}/assignment/publish` in `backend/src/api/roles.py`) and/or a Pulse tool that renders the current `assignment_brief` to PDF (reuse `_persist_assignment`/`render_assignment_pdf`), sets `assignment_problem_doc_key`, and flips `status` `draft → open`. Reuse the same status-flip in the existing manual upload endpoint (`roles.py:541-542`).

---

### 🔴 Bug 3 [DONE] — A just-applied candidate (fit_score still running) shows the brief marked "Pending"

**Root cause (FE + BE, no stage gating):**
- The candidate-detail API attaches the role brief to **every** application regardless of stage (`backend/src/api/v1_dashboard.py:425-429`).
- The FE `AssignmentSection` renders whenever a brief exists and shows "Pending" purely on brief/deadline presence (`frontend/src/app/(app)/candidates/[id]/page.tsx:469, 516-524`), mounted with no stage check (`:1523`).

**Target behavior:** a candidate sees the assignment only once they have actually **reached the assignment stage**. Pre-assignment candidates show nothing.

**Fix — files to touch end to end:**
- `frontend/src/app/(app)/candidates/[id]/page.tsx` — gate `AssignmentSection` on the candidate's stage: only render when `current_stage_key`/`stage_view` has reached `assignment` (or there is a real submission). Update the "Pending" badge to be stage-derived, not brief-presence-derived (`:469, 516-524, 1523`).
- `backend/src/api/v1_dashboard.py` — preferably only include `assignment_brief`/doc fields when the candidate has reached the assignment stage (`:425-429`); at minimum also send `current_stage_key` so the FE can gate cleanly.
- Note: once Bug 2 lands (no auto-generated briefs, brief only when the stage is selected), most of this noise disappears — but keep the stage gate so a future legitimately-assigned role doesn't leak the brief to early-stage candidates.

---

## Cluster B — Role display (`/roles`)

### 🟠 Bug 4 [DONE] — Evaluation dimensions/weights not visible on the saved role

**Root cause (FE only):** the data is stored (`db/base.py:113`), persisted on apply (`recruiter_chat.py:356`), returned by the API (`roles.py:56, 129`), and typed on the FE (`frontend/src/lib/types.ts:163`) — but the role detail page **never renders it** (`frontend/src/app/(app)/roles/[id]/page.tsx` has no `evaluation_spec` reference). It only appears in the draft editor (`ArtifactPanel.tsx:179-228`), so dimensions vanish after creation.

**Fix — files to touch end to end:**
- `frontend/src/app/(app)/roles/[id]/page.tsx` — add a read-only "Evaluation criteria" section reading `role.evaluation_spec.dimensions` (key, label, weight, what_good_looks_like, anti_signals). Mirror the layout already used in `ArtifactPanel.tsx:179-228`.
- (Optional) `frontend/src/app/(app)/roles/page.tsx` — show dimension count / top weights on the list card.
- No BE/DB change.

---

### 🔴 Bug 5 [WONTFIX — voice is mandated] — Screening is hardcoded/forced to "voice"; must be fully generic

**Root cause (FE + BE + pipeline/prompt):** voice is forced at every layer and the FE label is a literal string:
- FE prints the literal `"Voice (AI phone screen)"`, ignoring the role's real config (`frontend/src/app/(app)/roles/[id]/page.tsx:565-571`).
- BE forces `screening_modality="voice"`: DB default (`db/base.py:105-107`), `create_role` overwrites the arg (`tools.py:1035`), apply hardcodes it (`recruiter_chat.py:355`), and PATCH rewrites any change back (`roles.py:281-283`).
- Pipeline default always seeds a `voice_screen` stage (`pipeline.py:133`) and the draft prompt hardcodes it with a "copy as-is" rule (`tools.py:1793, 1819`).

**Target behavior:** screening is **super generic** — driven entirely by the actual selected pipeline stages and the user's choice. Voice is one option among others, not a forced default; "no screening" is valid; the role page renders whatever screening stage(s) the pipeline actually has.

**Fix — files to touch end to end:**
- `frontend/src/app/(app)/roles/[id]/page.tsx` — replace the hardcoded label (`:565-571`) with dynamic rendering derived from the role's pipeline stages (the screening/`voice_screen` stages present), or "No screening" when none. The list page already reads `r.screening_modality` (`roles/page.tsx:92-95`) — keep it dynamic once the BE stops forcing voice.
- `backend/src/recruiter_agent/tools.py` — stop overwriting the modality in `create_role` (`:1035`); make the draft-prompt pipeline (`:1793, 1819`) treat screening stages as optional/user-driven rather than "copy as-is with voice_screen".
- `backend/src/api/recruiter_chat.py` — stop hardcoding `screening_modality="voice"` on apply (`:355`); derive it from the chosen pipeline (or store "none").
- `backend/src/api/roles.py` — remove the force-to-voice in PATCH (`:281-283`) so the modality can actually change.
- `backend/src/db/base.py` — relax/justify the `screening_modality` default (`:105-107`); allow a generic value or make it nullable, since the pipeline stages are the real source of truth.
- `backend/src/models/pipeline.py` — make `voice_screen` in `DEFAULT_PIPELINE` (`:133`) optional rather than always-on (or document it as a default the user can drop).
- Consider deriving the displayed screening purely from `RolePipelineStage` rows and treating `screening_modality` as a denormalized convenience, to avoid the two ever disagreeing again.

---

## Cluster C — Chat panel UX

### 🟡 Bug 6 [DONE] — Role draft panel not scrollable / not editable after the role is created

**Root cause (FE):** both symptoms trigger when the artifact `status` flips to `"applied"` on role creation (`frontend/src/lib/useRecruiterChat.ts:607`):
- Edits are dropped: `readOnly = applied` and `patch()` early-returns (`ArtifactPanel.tsx:167-176`); Save/Apply disabled (`:775, 782`).
- Scroll is dead: the `overflow-y-auto` body also gets `pointer-events-none` when applied (`:344`), killing wheel/scrollbar interaction.
- Secondary: fragile height chain — `<main className="... overflow-y-auto">` + `h-full` instead of `min-h-0` (`frontend/src/app/(app)/layout.tsx:31-33`, `dashboard/page.tsx:23, 65`).

**Target behavior:** the panel is always scrollable; an applied role is still viewable and re-editable (edits route back through Bug 2's role-edit loop: changing the brief → re-post → PDF → open).

**Fix — files to touch end to end:**
- `frontend/src/components/recruiter-chat/ArtifactPanel.tsx` — remove `pointer-events-none` from the scroll body (`:344`) so it always scrolls; decouple editing from `applied` (allow edits + Save/re-Apply, or at minimum keep inputs focusable and the panel scrollable) (`:167-176, 775, 782`).
- `frontend/src/app/(app)/layout.tsx` + `frontend/src/app/(app)/.../dashboard/page.tsx` — tighten the height chain (`min-h-0` / `overflow-hidden`) so the inner `flex-1 overflow-y-auto` always has bounded height (`layout.tsx:31-33`, `dashboard/page.tsx:23, 65`).
- `frontend/src/lib/useRecruiterChat.ts` — on edit-after-apply, route Save through the role-update/assignment-post path rather than treating `applied` as terminal (`:607`).

---

## Priority + sequencing

| # | Bug | Sev | Layers | Lead change |
|---|-----|-----|--------|-------------|
| 2 | Assignment generated with no intent | 🔴 | BE | Gate generation on assignment stage + explicit ask; introduce `draft` status; PDF gates `open` |
| 5 | Screening forced to voice | 🔴 | FE+BE+pipeline | Make screening fully generic; stop forcing voice at all 4 BE points + FE label |
| 3 | Brief shown "Pending" pre-assignment | 🔴 | FE+BE | Stage-gate the candidate assignment section |
| 1 | Pulse can't read/edit/ask | 🟠 | FE+BE | `get_role` read tool + brief textarea in panel + ask-first prompt |
| 4 | Eval dimensions not visible | 🟠 | FE | Render `evaluation_spec` on role page |
| 6 | Panel not scrollable/editable | 🟡 | FE | Drop `pointer-events-none`; decouple edit from `applied` |

**Recommended order:** Bug 2 first (it defines the new lifecycle + `draft` status + PDF→active gate that Bugs 1 and 3 depend on), then Bug 5 (independent, self-contained), then 1 → 3 → 4 → 6.

**Cross-cutting theme:** the system currently *defaults assignments and voice ON and forces them across layers with no honored opt-out*, and views render off raw "data exists" presence instead of pipeline stage / user intent. The fixes invert both: existence and display follow the **selected pipeline stages** and an explicit **post → PDF → active** step.

**New DB/enum work introduced by the plan:** add `RoleStatus.DRAFT = "draft"` (`backend/src/models/candidate.py:70-73`) and an Alembic migration is not needed for the enum (status is a free `String(20)` column, `db/base.py:91`) — but seed/careers logic that assumes `open` must learn about `draft` (`roles.py:606` already filters `open`, so `draft` is hidden for free).

---

# Round 2 — additional issues

## 🔴 Bug 8 [TO_FIX] — Dynamic options (quick_replies) built wrong

**What was wanted (Claude-style):** the LLM proposes options that render **inside the main input bar**; the recruiter selects a chip **or** types their own; if there are multiple questions, the recruiter **navigates between them inside the input** and submits all at once.

**What the other agent built (wrong):**
- `quick_replies` tool takes a **single** question (`prompts.py` `QUICK_REPLIES_TOOL`); the prompt forces "one question per turn."
- Options render as an **inline chat bubble** (`ChatPanel.tsx` MessageBubble → `<QuickReplies>`), not in the input. The input (`PulseComposer`) is merely disabled (`useRecruiterChat.ts:358`, `dashboard/page.tsx:79`).
- No multi-question navigation.
- The system prompt is **hardcoded**: six fixed scoping questions with example option lists (`prompts.py:50-109`) and a hardcoded **PIPELINE TEMPLATES** table (`prompts.py:138-154`) + SYSTEM DEFAULTS table.

**Target / fix — files end to end:**
- `backend/src/recruiter_agent/prompts.py` — (a) `QUICK_REPLIES_TOOL` → accept a `questions: [{question, options, allow_custom}]` **array**; (b) de-hardcode the prompt: keep the section structure but remove the six canned questions and the pipeline table — replace with principle ("ask only what you can't infer; you may batch questions in one call; derive options from context; pick a sensible pipeline the recruiter edits in the panel"). Bump `RECRUITER_SYSTEM_VERSION`.
- `backend/src/recruiter_agent/runner.py` — quick_replies branch already passes `arguments` through + ends turn with `awaiting_quick_reply`; works with the array shape (FE reads `args.questions`). No structural change needed.
- `backend/src/api/recruiter_chat.py` — bump `ToolResultBody.content` max length (multiple answers exceed 500).
- `frontend/src/lib/useRecruiterChat.ts` — `QuickRepliesData` → `{ toolUseId, questions: [...] }`; expose top-level `pendingQuickReplies` state (not attached to a message) + a `submitQuickReplies(combined)` that calls `sendToolResult` and clears it; stop disabling the input as the answer surface.
- `frontend/src/components/recruiter-chat/PulseComposer.tsx` — when `pendingQuickReplies` is set, render the options UI **inside the composer** (chips + type-your-own + prev/next navigation + "i of n"), submit all answers on completion.
- `frontend/src/components/recruiter-chat/QuickReplies.tsx` — repurpose into the multi-question navigator widget used by the composer.
- `frontend/src/components/recruiter-chat/ChatPanel.tsx` — remove the inline `<QuickReplies>` render + `useRecruiterChatCtx` from MessageBubble.
- `frontend/src/app/(app)/dashboard/page.tsx` — pass `quickReplies` + `onQuickReplySubmit` into `PulseComposer`.
