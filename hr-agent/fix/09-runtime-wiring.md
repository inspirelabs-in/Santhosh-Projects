# 09 — Runtime Wiring: make the dynamic flow live (synthesized from 5-agent scan)

> **STATUS (2026-06-22): STAGE 1 DONE, STAGE 2 IN PROGRESS.** Stage 1 (dual-write `stage_key`, emit `domain_events`, rubric-driven scoring with local prompts, `[SCRAPE]` marks, in-passing bug fixes) is shipped. Supervisor retired; approval-gate backdoor (NB-1/2) killed; inbound-email funnel + threading (NB-12) shipped. **Stage 2 (flip progression onto `role_pipeline_stages`)** is the active build -- full spec in `10-generic-stage-runner.md`.

> Goal: a candidate entering a role runs the **role's configured pipeline**
> (`role_pipeline_stages`) via `transition()`/a reconciler, emitting
> `domain_events`, persisted; scoring reads `evaluation_spec` + `company_context`
> with **local prompts**. Plus the `[SCRAPE]` map for dead/legacy code.

---

## The live chain today (what we're rewiring)

```
careers-form / mail_ingest → run_intake (current_stage='applied')
  → run_apply_to_screening: parse → fit (RED=reject) → auto_progress()
  → auto_progress → _legacy_progress OR _template_progress(roles.pipeline_template JSONB)
        _fire_voice_screen → dispatch_voice_screening (set VOICE_SCREEN_SCHEDULED)
  → ElevenLabs webhooks → VOICE_SCREEN_* → evaluate_voice_call → auto_progress
        _fire_assessment → dispatch_assessment (ASSIGNMENT_SENT)
  → /apply/{token}/assignment → run_assignment_processing → REPORT_READY
        → TECHNICAL_PENDING_APPROVAL
  → HR books meeting via Pulse → chat_meeting.book_meeting (set *_MEETING_SCHEDULED)
  → Read.ai webhook → analyze_meeting → *_EVALUATED → auto_progress
        _fire_meeting → PARKS to NEEDS_HR_REVIEW (auto-scheduler disabled)
  → HR approval gates (/agentic/*/approve) → _kick_schedule_meeting → LEGACY schedule_meeting
  → HR decision → HIRED / REJECTED
```

Two state systems coexist: **legacy** `current_stage` (driven by `auto_progress`,
written via `set_stage()` in `db/repositories/v1_application.py:28`) and the
**new** `current_stage_key`/`stage_status` + `role_pipeline_stages` +
`state_machine.transition()` — which **nothing calls yet**.

## Cutover strategy: expand → dual-write → flip (low risk)

Two stages. Stage 1 is **additive and safe** (nothing changes how candidates
progress); Stage 2 flips progression onto the role's pipeline.

### STAGE 1 — Safe, additive (do first)

**1a. Dual-write `stage_key` at the single choke point.** In
`set_stage()` (`v1_application.py:28`), after `app.current_stage = new_stage.value`,
also set `current_stage_key`/`stage_status` by mapping the legacy stage →
`stage_key` (reuse the CASE map already in `0031_phase0_foundation.py:250-272` as
a Python helper `_legacy_stage_to_key`). Best-effort, non-fatal. → the new columns
now track reality without touching progression.

**1b. Emit `domain_events`.** From `set_stage()` (or `auto_progress`), call
`db.events.emit_event(type="stage_changed", ...)`. At manual gates emit
`requires_action=true` events (`assessment_review`, `interview_scheduling_needed`,
`decision_pending`) → these populate the future inbox + the candidate trace.

**1c. Rubric-driven scoring with local prompts.** Wire `role.evaluation_spec` +
`role.company_context` into the scoring activities + their local prompt constants:
| Stage | Activity | Prompt constant | Inject at |
|---|---|---|---|
| fit | `activities/fit_score.py:163-198` | `FIT_SCORE_V1` (replace hardcoded `_DEFAULT_WEIGHTS` + GrabOn block) | role_snapshot + compile_prompt |
| screening gen | `v1_generate_screening.py:47-59` | `SCREENING_GEN_V1` | compile_prompt |
| screening eval | `v1_evaluate_screening.py:44-59` | `SCREENING_EVAL_V1` | compile_prompt |
| voice gen | `v1_voice_screening.py:147-156` | `VOICE_SCREEN_GEN_V1` | compile_prompt |
| voice eval (text) | `v1_evaluate_voice_call.py:215-224` | `VOICE_SCREEN_EVAL_V1` | compile_prompt |
| voice eval (audio) | `services/gemini_audio_eval.py:139-151` | `_EVAL_PROMPT` | format call |
| assignment parse | `v1_parse_assignment.py:178-189` | `ASSIGNMENT_PARSE_V1` | compile_prompt |
| meeting analysis | `v1_meeting_analysis.py:58,70` | `MEETING_ANALYSIS_V1` (replace `scoring_rubric` with `evaluation_spec`) | compile_prompt |

Prompts are edited **locally** (the `get_prompt(name, fallback=CONST)` fallback in
`llm/prompt_manager.py:54` uses the constant when Langfuse is absent).

### STAGE 2 — Flip progression onto `role_pipeline_stages`

Rewire `auto_progress` so the **source of truth is `role_pipeline_stages`**, not the
legacy hardcoded chain or the old `pipeline_template` JSONB:
- On a stage-complete event, `role_pipeline_stage_repo.next_stage(role_id, current_stage_key)`.
- Map `stage_type → fire`: `voice_screen→_fire_voice_screen`, `assignment→_fire_assessment`,
  `assessment_review|interview|decision→park + emit requires_action`, `offer→offer activity`.
- If the next stage's `mode == manual` → `transition(..., PARKED)` + `requires_action` event (inbox).
- If `mode == auto` → fire the activity.
- **Remove resume screening / reorder / add a 2nd tech round** all "just work" because
  progression walks the role's actual stage rows.
- Re-point the HR **approval gates** (`/agentic/technical|ceo/approve`,
  `_kick_schedule_meeting`) away from the legacy `schedule_meeting` to park + notify
  (chat-driven `book_meeting` stays the booking path). Kills the NB-1/NB-2 backdoor.

## Bugs to fix in passing (found by the scan)

- **Voicemail classifier** reads `answer`, stored as `answer_transcript`
  (`classifiers/voicemail.py:41,99`) → detection is a silent no-op. Fix the key.
- **`needs_hr_review` is dead on the text voice path**: `VOICE_SCREEN_EVAL_V1`
  (+ Gemini `_EVAL_PROMPT`) only emit `clear_pass|clear_reject`, but the evaluator
  branches on `needs_hr_review`. Add it to the prompt or drop the branch.
- **`auto_progress._fire_chat_screen` (`:215`) imports `run_apply_to_chat`** which
  no longer exists → `ImportError` if any role template uses `chat_screen`. Remove/guard.
- **Graph webhook drops new applicants** (`webhooks_email.py:91`) — only known
  candidates handled. Note for intake hardening.

---

## [SCRAPE] map (reconciled across all 5 scans)

### Mark `# [SCRAPE]` now — TRULY DEAD, no live caller
- `agent/generators.py` → `gen_tailored_questions()`, `extract_turn()` (Chat-V2)
- `agent/prompts/tailored_qs.py`, `agent/prompts/extract.py` (Chat-V2 prompts)
- `agent/schemas.py` → `TailoredQuestion`, `TailoredQuestionsOut`, `ExtractedTurn` (**keep `AssignmentBriefOut` — live**)
- `activities/score_responses.py` (whole file — Temporal-era, no importers)
- `activities/report.py` → `run_interview_report`, `interview_report_activity` (superseded by `v1_meeting_analysis`/`v1_journey_report`)
- `activities/classify.py` → `run_classify`, `classify_activity` (no callers; intake classifies inline)
- `activities/schedule.py` → `run_book_interview`, `book_interview_activity` (**keep `run_propose_slots` — live**)
- `services/calendar_sync.py` → `create_event`, `cancel_event` (**keep `find_free_slots` — live**)
- `services/auto_progress.py:503-548` commented legacy auto-scheduler block (delete)

### Mark `# [SCRAPE?]` + reason — NEEDS A DECISION (don't delete yet)
- **Supervisor** (`supervisor/engine.py`,`perception.py`,`guardrails.py`,`autonomy.py` + `supervisor_*` tables + `typed_event_bus.py`): never runs (CRASH-3). **Recommend: retire** — `domain_events` replaces it. Mark `[SCRAPE]` + disable the lifespan task (`api/main.py:100`). (Alt: 1-line fix `prompt_version=`.)
- `activities/v1_schedule_meeting.py:schedule_meeting` — reachable only via the HR approval gates; becomes scrapable once Stage 2 re-points them to park.
- `services/panel_availability.py` + `services/smart_scheduler.py` — legacy auto-scheduler; reachable via a manual trigger endpoint + Arq jobs. Mark legacy; decide whether HR still uses the manual panel-availability trigger.
- `activities/screening.py:run_send_screening` — text-screening, superseded by voice (all roles `screening_modality='voice'`). Reachable via old dashboard buttons.
- `api/dashboard.py:_signal_workflow` — Temporal stub, always returns False (no server). No-op.

---

## Open decisions for the "we'll talk" checkpoint
1. **Supervisor**: retire (mark `[SCRAPE]` + disable lifespan) vs keep (1-line fix)? → recommend **retire**.
2. **Approval gates**: re-point `/agentic/*/approve` to park + notify (kill the legacy `schedule_meeting` backdoor)? → recommend **yes**.
3. **Execution order**: Stage 1 (safe: dual-write + events + rubric scoring + `[SCRAPE]` marks + the in-passing bug fixes) first, then Stage 2 (progression flip)? → recommend **yes**.
