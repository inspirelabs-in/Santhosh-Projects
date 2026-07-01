# HR Agent — Architecture & Full Crack Audit

> Deep forensic audit as of 2026-07-01.
> 5 parallel agents read every file in backend/src.
> Covers data flow, stage-by-stage data availability, and every bug found.

---

## Data Flow

### End-to-End Pipeline

```
Candidate ──────────────────────────────────────────────────────────────┐
  [Web Form]  → POST /webhooks/careers-form                             │
  [Email]     → IMAP poller / Graph webhook → mail_ingest.py            │
  [Direct]    → POST /apply                                             │
                         │                                              │
                         ▼                                              │
              ┌──────────────────────┐                                  │
              │  intake              │  dedup → upsert_candidate        │
              │                      │  create_application (role_id?)   │
              │                      │  upload_resume → R2              │
              │                      │  capture_consent                 │
              └──────────┬───────────┘  send ack email                  │
                         │ WARNING: role_id may be NULL here            │
                         ▼                                              │
              ┌──────────────────────┐                                  │
              │  parse_resume        │  download R2 → LLM extract       │
              │                      │  → CandidateProfileRow           │
              │                      │  → pgvector embedding            │
              └──────────┬───────────┘  needs_hr_review IGNORED         │
                         │                                              │
                         ▼                                              │
              ┌──────────────────────┐                                  │
              │  fit_score           │  profile × JD → tier             │
              │                      │  GREEN / AMBER / RED             │
              │                      │  CRASH if jd_text is NULL        │
              └──────┬───────┬───────┘                                  │
                     │       │                                          │
               GREEN/AMBER  RED ──────────────────────── REJECTED       │
                     │                                                  │
                     ▼                                                  │
              ┌──────────────────────┐                                  │
              │  voice_screen        │  ElevenLabs call                 │
              │  (only path now)     │  → transcript → eval             │
              │                      │  rejection = NO email sent ←BUG  │
              └──────────┬───────────┘                                  │
                         │                                              │
                         ▼                                              │
              ┌──────────────────────┐                                  │
              │  assignment_sent     │  email brief + deadline          │
              │                      │  stage set BEFORE email ←BUG    │
              └──────────┬───────────┘                                  │
                         ▼                                              │
               ┌──────────────────────┐                                  │
               │  tech_interview      │  chat-driven via Pulse           │
               │  ceo_interview       │  book_meeting (idempotent)       │
               │  hr_interview        │  → meeting_analysis              │
               └──────────┬───────────┘                                  │
                         │                                              │
              ┌──────────┴───────────┐                                  │
              │  offer               │  email only                      │
              │                      │  NEVER sets stage=HIRED ←BUG    │
              └──────────┬───────────┘                                  │
                         │                                              │
                   HIRED (manual) / REJECTED ◄──────────────────────────┘
```

### Stage-by-Stage Data Availability

| Stage | Data REQUIRED | Data PRODUCED | What's NOT available yet |
|-------|--------------|---------------|--------------------------|
| intake | sender_email/phone/name, r2_keys | candidate, application (role_id may be null), consent | profile, fit_score, screening, voice |
| parse_resume | r2_key, candidate_id | CandidateProfileRow, embedding, evidence | fit_score, JD context |
| fit_score | CandidateProfileRow, Role.jd_text | app.fit_score, app.fit_tier | screening answers, voice eval |
| screening gen | Role, CandidateProfile (snapshot) | app.screening_questions | voice answers, assignment |
| voice dispatch | Candidate.phone!, Role, CandidateProfileRow | VoiceCall row, questions | transcript, evaluation |
| voice eval | VoiceCall.answers, Role, CandidateProfileRow | VoiceCall.evaluation, profile enrichment | assignment, interviews |
| assignment | Role.assignment_brief, Candidate.email | AssignmentRow (maybe), submission later | interview data |
| meeting analysis | MeetingSession.transcript_r2_key, Role.scoring_rubric | MeetingSession.report, scores | subsequent rounds |
| ceo_brief | fit_score?, voice_block?, technical round? | journey_report | all may be null |
| offer | Candidate.email, Role | DecisionRecord | HIRED stage never set |

### Recruiter Pulse (Live Recruiter Agent)

```
Recruiter browser
      │  SSE  GET /recruiter-chat/{conv_id}/stream
      │
      ▼
api/recruiter_chat.py
      │  _sse_loop: Redis BLPOP inbox
      │
      ▼
recruiter_agent/runner.py  (run_recruiter_turn)
      │  _build_history_for_llm  ← STRIPS pending-confirm rows ←BUG
      │  LLM call (LiteLLM/Claude)
      │  iterate tool_calls
      │    ├── non-confirm tools → call_tool() → persist → yield SSE
      │    └── confirm-gated tools → save Redis pending → yield confirm_card
      │
      ▼ (confirm card on browser)
      │
      ├── user CONFIRM → POST /recruiter-chat/{id}/confirm
      │     body.edited_args merged WITHOUT validation ←BUG
      │     Redis GET confirm → _execute_confirmed → call_tool
      │
      └── user CANCEL → enqueue "I cancelled" text
            runner sees original request unanswered → re-proposes ←BUG
```

### Voice Screen Detail

```
auto_progress
      │ _fire_voice_screen()
      ▼
dispatch_voice_call / dispatch_voice_screening (two paths, differ in richness)
      │  generate questions (LLM, using CandidateProfile snapshot)
      │  set_stage(VOICE_SCREEN_SCHEDULED)  ← BEFORE ElevenLabs call ←BUG
      │  POST ElevenLabs API
      │  mark_dispatched(provider_call_id)  ← separate session
      ▼
ElevenLabs conducts call
      │  webhook: conversation_started → mark_in_progress
      │  webhook: post_call_transcription
      │    claim_completion() CAS  ← sentinel can orphan on crash ←BUG
      │    upload transcript → R2
      │    set_stage(VOICE_SCREEN_COMPLETED)
      │    enqueue evaluate_voice_call  ← paralinguistic serialized as dict ←BUG
      ▼
evaluate_voice_call (Arq job)
      │  voicemail classifier  ← reads 'answer' key, stored as 'answer_transcript' ←BUG
      │  LLM eval with role+JD+answers
      │  save_evaluation
      │  set_stage(VOICE_SCREEN_EVALUATED) ← first session
      │  re-score fit  ← second session (race window) ←BUG
      │  auto_progress  ← no rejection email sent if rejected ←BUG
```

### Meeting Scheduling (3 Uncoordinated Paths) [FIXED]

```
auto_progress._fire_meeting()           ← NOW parks + nudges (chat-driven)
      │
      ├─► panel_availability.initiate_panel_availability()   ← DISABLED
      ├─► smart_scheduler.initiate_smart_schedule()          ← DISABLED
      └─► schedule_meeting() (legacy fallback)               ← DISABLED
                                    ↑
                    _fire_meeting now calls set_stage(NEEDS_HR_REVIEW)
                    and publishes meeting_scheduling_needed event.
                    Recruiter schedules via Pulse chat (book_meeting).
                    book_meeting has idempotency check for existing sessions.
```

### Background Workers (Arq + FastAPI lifespan)

| Worker | Trigger | Key function |
|--------|---------|-------------|
| mail_poller | IMAP idle / Graph subscription | parse raw emails → intake |
| stall_detector | 5-min loop | detects stalled pipeline stages, alerts HR |
| auto_nudge | 1-hour loop | candidate follow-up nudges |
| recruiter_nudge | 15-min loop | surfaces stuck candidates to recruiter |
| webhook_watchdog | 30-min loop | recovers stalled voice calls → NO CAS guard ←BUG |
| supervisor_loop | event-driven | cross-pipeline decisions → BROKEN (TypeError) ←BUG |
| config_invalidator | Redis pub/sub | clears Langfuse prompt cache |

---

## All Cracks — Organized by Subsystem

---

### CONFIRMED CRASHES (fix immediately, these throw in production)

| ID | File:Line | What crashes | Trigger |
|----|-----------|-------------|---------|
| CRASH-1 | `pipeline/v1.py:463` | `AttributeError: 'str' has no attribute 'description'` | Any screening with red_flags that is not `clear_pass`. `evaluation.red_flags` is `list[str]` but code does `f.description` |
| CRASH-2 | `activities/fit_score.py:~180` | `TypeError: 'NoneType' is not subscriptable` on `role.jd_text[:8000]` | Any newly-created role where JD hasn't been filled in |
| CRASH-3 | `supervisor/engine.py:292-308` | `TypeError: complete() missing required argument 'prompt_version'` on every supervisor LLM call | Always — supervisor has NEVER worked in production |
| CRASH-4 | `api/webhooks_voice.py:1268-1278` + `v1_evaluate_voice_call.py:205` | `AttributeError: 'dict' object has no attribute 'model_dump'` | Every Arq-queued voice evaluation where emotion data is present |

---

### RECRUITER PULSE GRAPH — State Machine Bugs

**CRITICAL**

**G-1 — Cancel re-triggers the same action** `runner.py:641-644`, `api/recruiter_chat.py:428-439`

Root cause: `_build_history_for_llm` strips ALL rows tagged `awaiting_confirmation` (both the assistant tool-call row AND the tool result row). When the cancel text `"I cancelled the pending action (xyz). Move on."` arrives as the next user message, the LLM history shows:
- User: "create a role for Senior Backend Engineer"
- (nothing — the proposal was stripped)
- User: "I cancelled the pending action (xyz). Move on."

The LLM sees an unanswered original request and re-proposes `create_role_with_assignment`. Confirm card appears again.

**Fix:** When building history for a post-cancel turn, inject a synthetic assistant + tool-result pair showing the action was proposed and user cancelled. OR: don't strip the pending-confirm pair — let the model see its own rejected proposal.

---

**HIGH**

**G-2 — `edited_args` security injection** `api/recruiter_chat.py:412-418`

Frontend can send arbitrary `edited_args` in `ConfirmBody`. These are merged with `.update()` directly into the stored args with zero schema validation. A recruiter can inject `pipeline_template: ["offer"]` (skip all screening) or any internal key.

**Fix:** Validate against the tool's declared parameter schema before merging. Strip `_`-prefixed keys. Re-run RBAC on mutated args.

---

**G-3 — Tool output lost if SSE drops mid-turn** `api/recruiter_chat.py:556`, `runner.py:1089-1114`

`call_tool()` can run (e.g., send email, update stage) but if the client disconnects before `append_message` executes, the action happened with no DB audit trail. On reconnect, no re-send occurs.

---

**G-4 — Multi-tool batch with one confirm breaks history** `runner.py:1000-1085`

If the LLM returns e.g. `smart_defaults_for_role + create_role_with_assignment` in one response, the first tool executes, the second sets `confirm_pending=True`. The assistant row has both tool_calls. The history safety pass strips only `create_role_with_assignment`'s tool_call (the orphaned one), leaving `smart_defaults_for_role`'s tool_call with no matching result. The cancel→re-proposal path (G-1) then fires.

---

**MEDIUM**

**G-5 — Redis key deleted before execution** `runner.py:365`
`_consume_pending_confirm` deletes the Redis key before the tool executes. On crash between delete and tool call, the confirm card is permanently non-functional.

**G-6 — Expired confirm falls into LLM loop** `runner.py:798-799`
After 10-min TTL, Redis key expires. `_consume_pending_confirm` returns None. The `__pulse_confirm__:<id>` text passes into the LLM as a user message. The model produces a confused response instead of "confirm expired, please try again."

**G-7 — Last hop doesn't enforce `tool_choice="none"`** `runner.py:68,821`
On hop 4, a system message says "stop calling tools." On hop 5, `tools=RECRUITER_TOOLS` and `tool_choice="auto"` are still passed. Model can still emit tool_calls. Loop exits with `final_text=""` and an empty assistant message persisted.

**G-8 — SSE reconnect after mid-confirm-drop** `api/recruiter_chat.py:474-506`
If SSE drops after cancel was partially processed (cancel message consumed, runner mid-stream), on reconnect: no cancel in inbox, Redis key already gone. Confirm card is visible in message history but both Confirm and Cancel silently fail.

**G-9 — `create_role` DB write has no rollback if assignment gen fails** `tools.py:1039`, `runner.py:395`
`create_role_with_assignment` creates the role row then generates the assignment. If assignment gen fails, the role row is orphaned in DB with no cleanup.

---

### PIPELINE STAGE DATA FLOW — Ordering & Race Bugs

**HIGH**

**P-1 — Stage advanced BEFORE email/call succeeds**

| Stage | File | Line | What commits early |
|-------|------|------|--------------------|
| assignment | `v1_dispatch_assessment.py` | 49-55 | `ASSIGNMENT_SENT` set before `send_assignment_email` |
| voice dispatch | `v1_voice_screening.py` | 286 | `VOICE_SCREEN_SCHEDULED` set before ElevenLabs HTTP call |
| meeting | `v1_schedule_meeting.py` | 268 | `*_MEETING_SCHEDULED` set before bot dispatch & confirmation call |

If the subsequent call fails, stage is stuck advanced with no communication sent and no automatic rollback.

---

**P-2 — Voice rejection never sends rejection email** `v1_evaluate_voice_call.py:219-234`

`evaluate_voice_call` sets stage to `REJECTED` and writes an audit row but NEVER calls `run_rejection`. The candidate is silently rejected with zero communication. Contrast: `pipeline/v1.py` rejection path calls `auto_progress` which does dispatch rejection in some paths.

---

**P-3 — Offer activity never sets stage to HIRED** `activities/offer.py` (entire file)

`generate_offer` sends the offer email and writes `DecisionRecord` but does not call `set_stage(HIRED)`. The stage stays at `CEO_PENDING_APPROVAL` or `HR_EVALUATED`. HIRED requires manual HR dashboard action.

---

**P-4 — Voice evaluator `auto_progress` races fit re-score** `v1_evaluate_voice_call.py:308-333`

1. Session 1 commits: stage → `VOICE_SCREEN_EVALUATED`
2. `run_fit_score` runs outside session (can take 2-10s)
3. `auto_progress` can read `VOICE_SCREEN_EVALUATED` and fire `_fire_assessment` concurrently
4. Session 2 commits: fit re-score sets stage → `REJECTED`

Candidate is simultaneously being moved to assessment AND rejected.

---

**P-5 — Webhook double-fire dispatches duplicate assessment** `v1_evaluate_voice_call.py:335`

ElevenLabs retries webhooks. If `post_call_transcription` fires twice and both pass the `claim_completion` CAS (see V-C1/C2), `evaluate_voice_call` runs twice → two `auto_progress` calls → two `enqueue("dispatch_assessment")` jobs with no unique Arq key → duplicate assessment emails.

---

**P-6 — `auto_progress` TOCTOU race** `services/auto_progress.py:66-83`

Reads `app.current_stage` inside session, closes session, fires actions. Between close and action, HR or another worker can advance the stage. No optimistic lock or compare-and-swap on the dispatch.

---

**MEDIUM**

**P-7 — No-profile fit_score never writes to DB** `activities/fit_score.py:148-158`

Early-return fast-path (no profile found) makes a `RED` decision and returns but never reaches the `application.fit_score = overall` write (line 224). `app.fit_score` and `app.fit_tier` remain NULL even though the candidate was effectively red-flagged.

**P-8 — `needs_hr_review` silently ignored** `pipeline/v1.py:~148`

`ParseResumeResult.needs_hr_review` is returned but `run_apply_to_screening` ignores it entirely. Low-confidence OCR parses proceed to fit_score and voice with no HR notification.

**P-9 — `AttributeError: 'str' has no attribute 'description'` — already in CRASH-1 above**

**P-10 — `AssignmentRow.deadline_at` not set if row missing** `v1_send_assignment.py:114-117`

`get_assignment()` can return None if the row wasn't pre-created. The `assignment.deadline_at = ...` is silently skipped. Stall detector and nudge worker never fire for this assignment.

**P-11 — `expected_ctc_lpa` confidence mapped from wrong field** `activities/parse_resume.py:342`

```python
"expected_ctc_lpa": fc.current_ctc_lpa,  # BUG: should be fc.expected_ctc_lpa
```
Evidence record for expected CTC shows confidence from current CTC.

**[FIXED] P-12 — Duplicate `MeetingSession` rows possible** `v1_schedule_meeting.py`, `v1_dispatch_meeting_bot.py`

**[FIXED]** `book_meeting()` in chat_meeting.py checks for existing active sessions (pending/scheduled/in_call) before creating a new row. The auto_progress no longer triggers the old v1_schedule_meeting path automatically.

**P-13 — `candidate.phone` required even when confirmation call disabled** `v1_schedule_meeting.py:123`

Candidates who reached interview stage with a null phone number are hard-blocked here even if `enable_voice_meeting_confirmation=False`.

**P-14 — `bot_id` column semantically overloaded** `v1_schedule_meeting.py:266`

`meeting_row.bot_id = ms_meeting_id` stores the Graph meeting ID in the bot_id column. Recall.ai bot dispatch then overwrites it. For Read.ai, the Graph ID stays indefinitely.

**P-15 — Latest voice call in CEO brief may be a failed retry** `v1_ceo_brief.py:70`

`voice_calls[0]` orders by `created_at DESC`. The latest may be a `NO_ANSWER` retry while an earlier row has the actual evaluation.

---

### VOICE SCREEN — Webhook Race Conditions

**CRITICAL**

**V-C1 — Orphaned sentinel permanently locks re-delivery** `db/repositories/voice_call.py:125-148`

`claim_completion` writes sentinel `_in_flight:{conversation_id}` as a CAS. Server crash after this write but before `save_call_completion` → all subsequent ElevenLabs re-deliveries see `already_completed=True` and return `{"duplicate": True}`. Candidate is permanently stuck. Watchdog can recover (up to 2h) but watchdog itself has no CAS (see V-C3).

**V-C2 — Recovery endpoint has no CAS guard** `api/webhooks_voice.py:1309-1463`

`/elevenlabs/recover/{conversation_id}` checks `voice.transcript_r2_key` but does NOT call `claim_completion`. Concurrent watchdog + manual recovery → both see `None`, both upload to R2, both call `save_call_completion`, both enqueue `evaluate_voice_call` → evaluator fires twice → duplicate decisions, double stage transitions.

**V-C3 — Watchdog recovery also has no CAS guard** `services/webhook_watchdog.py:110-155`

`_try_recover_from_elevenlabs` calls `save_call_completion` and enqueues evaluator with no sentinel or CAS. Three concurrent paths (late webhook + watchdog + manual recovery) can all independently fire the evaluator.

---

**HIGH**

**V-H1 — Evaluation failure leaves candidate stuck at `VOICE_SCREEN_COMPLETED` forever** `v1_evaluate_voice_call.py:185-220`

LLM call or `save_evaluation` fails → Arq job fails → candidate stays at `VOICE_SCREEN_COMPLETED` indefinitely. Watchdog only monitors `SCHEDULED` and `IN_PROGRESS`, not `COMPLETED`. No alert fires.

**V-H2 — ORM object used after session closed** `api/webhooks_voice.py:553-570,853`

`voice` ORM object loaded in first `session_scope`. After scope exits, line 853 accesses `voice.questions`. JSONB attributes are safe (eagerly loaded), but any lazy-loaded relationship added later raises `DetachedInstanceError`.

**V-H3 — `_zip_answers` silently produces all-empty answers on TTS rephrasing** `api/webhooks_voice.py:352-441`

If ElevenLabs TTS significantly rephrased a question (failing 12-char substring + 5-shared-token heuristics), answer anchors are None → all Q/A entries get `answer_transcript=""`. Candidate scored as having given blank answers. No audit log entry for this failure path.

---

**MEDIUM**

**V-M1 — Voicemail classifier reads wrong key** `classifiers/voicemail.py:98`

Classifier does `a.get("answer", "")`. Stored answers use key `answer_transcript` (from `_zip_answers`). All lookups return empty string. Voicemail detection in the evaluator is a **complete no-op** — every voicemail passes through to full evaluation.

**V-M2 — Arq serializes `EmotionFeatures` as raw dict** `api/webhooks_voice.py:1268-1278`

Arq serializes `paralinguistic=EmotionFeatures(...)` as a plain dict. Evaluator calls `paralinguistic.model_dump()` → `AttributeError`. Paralinguistic features are never persisted via the normal queue path. `BackgroundTasks` path works (passes object directly), masking this in local testing.

**V-M3 — `format_context_for_prompt` reads wrong key names** `services/voice_context.py:205-207`

Reads: `experience_years`, `current_ctc`, `expected_ctc`, `notice_period`, `location`
Stored as: `total_experience_years`, `current_ctc_lpa`, `expected_ctc_lpa`, `notice_period_days`, `current_location`

All lookups return None. Extracted candidate facts (CTC, notice period, location) never appear in confirmation, status, or joining call prompts.

**V-M4 — Retry path uses impoverished system prompt** `activities/v1_voice_screening.py:52-158`

Retry calls use old `dispatch_voice_screening` with hardcoded `_build_system_prompt()` — no fit score, no screening verdict, no previous call summaries. New `dispatch_voice_call` uses `voice_context.py + voice_prompts.py` for rich context.

**V-M5 — No `UNIQUE` constraint on `provider_call_id`** `db/base.py:267`

Any duplicate (ElevenLabs bug) → `MultipleResultsFound` in `get_by_provider_id` → unhandled 500 → ElevenLabs retries indefinitely.

---

### PROMPTS — Data Unavailability & Hallucination Traps

| ID | Prompt | Stage | Variable | Available? | Impact |
|----|--------|-------|----------|------------|--------|
| PR-1 | `FIT_SCORE_V1` | fit_score | `role.jd_text[:8000]` | Crashes if None | Hard crash (see CRASH-2) |
| PR-2 | `VOICE_SCREEN_GEN_V1` Q5 | voice dispatch | `<city from resume if known>` | Often null | Aria speaks "You're based in None. The role is…" verbatim |
| PR-3 | `VOICE_SCREEN_GEN_V1` Q1/Q2 | voice dispatch | specific project from resume | Empty if projects=[] | LLM hallucinates a project name |
| PR-4 | `CEO_BRIEF_V1` | ceo_interview | `fit_score` | Nullable (see P-7) | Brief says "Fit score: n/a / 100 (n/a)" |
| PR-5 | `CEO_BRIEF_V1` | ceo_interview | `assessment_json` | Empty if no PI test | LLM invents behavioral/cognitive content |
| PR-6 | `CEO_BRIEF_V1` | ceo_interview | `voice_call_json` | Empty if voice failed | LLM fabricates phone screen summary |
| PR-7 | `CEO_BRIEF_V1` | ceo_interview | `technical_meeting_json` | Empty if no tech round | LLM invents technical interview analysis |
| PR-8 | `JOURNEY_REPORT_V1` | post-screening | `assignment_summary_json` | Always `{}` at report time | "Assignment Review" section is LLM filler |
| PR-9 | `JOURNEY_REPORT_V1` | post-screening | `screening_responses_json` | May be `[]` | LLM invents candidate quotes |
| PR-10 | `MEETING_ANALYSIS_V1` | interview | `scoring_rubric_json` | Nullable for new roles | LLM produces generic scores unrelated to role |
| PR-11 | `MEETING_ANALYSIS_V1` | interview | `transcript_json` | Truncated at 24k chars mid-JSON | LLM scores on incomplete transcript; 90-min+ interviews lose late content |
| PR-12 | `ASSIGNMENT_PARSE_V1` | assignment submitted | `assignment_brief` | Nullable | LLM checks "followed instructions" with no instructions |
| PR-13 | `SCREENING_EVAL_V1` | screening eval | `responses_json` | Stale in-memory snapshot | Evaluates wrong answers if candidate updated post-load |
| PR-14 | `INTERVIEW_REPORT_V1` | (any interview) | `{interviewer_names}`, `{competencies_list}` | Never filled — dead code | Unused template; would render literal placeholders if called |
| PR-15 | `ASSIGNMENT_GEN_V1` | prewarm | `screening_answers_json` | `{}` at prewarm time | Assignment can't calibrate to candidate strengths |
| PR-16 | `VOICE_SCREEN_EVAL_V1` | voice eval | `paralinguistic_json` | Often `{}` (race) | Audio analysis section is always empty |

---

### MEETING SCHEDULING — Double-Booking & Calendar Bugs

**CRITICAL**

**[FIXED] MS-C1 — Three paths, no mutex, no unique constraint** `services/auto_progress.py:451-498`

**[FIXED]** `_fire_meeting()` now parks candidate at NEEDS_HR_REVIEW and publishes a `meeting_scheduling_needed` event instead of chaining 3 scheduling paths. No duplicate rows from auto_progress. `book_meeting()` in chat_meeting.py also has idempotency check for existing sessions.

---

**HIGH**

**MS-H1 — Global busy-block instead of per-interviewer check** `services/scheduling.py:88-116`

`_existing_meetings_for_panel` has no per-attendee filter. One meeting anywhere in the 14-day window blocks ALL panels globally. Real-time Graph check always fails silently (MS-H2).

**MS-H2 — Graph calendar check always silently fails** `services/scheduling.py:227-232`

`organiser_email=round_cfg.panel_emails[0]` — Graph requires delegated access to the target calendar. Using a random panel member always fails. `_graph_busy_intervals` returns None. Scheduling falls back to the broken DB-only blocker.

**MS-H3 — Rejected candidate can self-book interview** `services/panel_availability.py:586-697`

`record_candidate_selection` does not re-read `app.current_stage`. Candidate token has 5-day TTL. If rejected after slots were sent but before candidate clicked the link, token still works and overwrites `rejected` stage with `*_meeting_scheduled`.

**MS-H4 — Manual schedule allows booking rejected candidates** `api/meetings.py:63-153`

`manual_schedule_meeting` creates meeting without checking `app.current_stage` or `app.status`.

**MS-H5 — Read.ai webhook accepts unauthenticated payloads** `api/webhooks_meeting.py:305-313`

When `read_ai_webhook_secret` is not configured, auth check returns silently (no-op) instead of raising 401. Anyone can POST fake transcripts → trigger fabricated evaluation.

**MS-H6 — Time-window transcript match assigns to wrong candidate** `api/webhooks_meeting.py:369-392`

When `meeting_url` is absent in Read.ai webhook, fallback is `get_by_time_window(..., window_minutes=30)`. Two interviews in the same 30-min window → transcript goes to wrong candidate. Code has a comment acknowledging this.

**MS-H7 — Smart scheduler TOCTOU: commit then book** `services/smart_scheduler.py:600-609`

```python
await session.commit()   # releases DB lock
await book_confirmed_meeting(...)  # external call AFTER lock released
```
Another scheduler can observe the same slot available in this window.

---

### SUPERVISOR ENGINE

**CRITICAL**

**SV-C1 — Supervisor never works: missing `prompt_version` arg** `supervisor/engine.py:292-308`

```python
result = await client.complete(...)  # missing prompt_version= (required, no default)
```
Raises `TypeError` on every call, caught by `except Exception`, exhausts 3 retries, re-raises. The supervisor engine has **never executed a reasoning action in production**.

---

**HIGH**

**SV-H1 — Supervisor races with `auto_progress` on meeting scheduling** `supervisor/engine.py:582-600`

Both `supervisor.schedule_interview` and `auto_progress._fire_meeting` can be triggered by the same `STAGE_CHANGED` event. No distributed lock between them. Both call `initiate_panel_availability` or `initiate_smart_schedule` → duplicate MeetingSession rows and invite emails.

**SV-H2 — Safety demotion mechanism never fires** `supervisor/guardrails.py:249-250`

```python
all_rejected = all(a.rejected_by is not None for a in recent)
```
Shadow-mode actions are never explicitly "rejected" (they just sit as `executed=False`). `rejected_by` is always None → `all_rejected` is always False → the 3-consecutive-wrong-action auto-demotion never fires. Supervisor can spam incorrect actions indefinitely.

**SV-H3 — Stale context snapshot** `supervisor/engine.py:316-319`

`build_context()` reads stage at event-claim time. LLM call takes 2-10s. `_execute_via_registry` then uses the stale `context.current_stage` for the DAG check, missing any stage advances that happened during LLM latency.

**SV-H4 — Rejected candidates counted as active siblings** `supervisor/perception.py:316-340`

Sibling query filters `.where(Application.status == "active")`. If `auto_progress` sets `current_stage=rejected` but `Application.status` is not updated atomically, rejected candidates appear as active siblings, skewing leniency calibration.

**SV-H5 — Supervisor system prompt hardcoded, not in Langfuse** `supervisor/engine.py:93-262`

The entire supervisor system prompt is a multi-line string in code. Cannot be hot-patched without a deploy.

---

### LANGFUSE / LLM INFRASTRUCTURE

**HIGH**

**LF-H1 — Permanent failure on startup outage** `llm/prompt_manager.py:34-51`

One-shot init: if Langfuse is unreachable at process start, `_langfuse_init_attempted=True` and `_langfuse_client=None` permanently. No retry ever. Prompts fall back to hardcoded strings for the entire process lifetime.

**LF-H2 — Mixed prompt versions per worker** `llm/prompt_manager.py:27-28`

Each Uvicorn/Gunicorn worker has its own in-memory cache (`_CACHE_TTL_SECONDS=60`). After a Langfuse update, workers with unexpired caches serve the old prompt. With N workers, the mixed-version window is up to `N × 60s`. No Redis-backed shared cache.

---

**MEDIUM**

**LF-M1 — Cache miss discards last good value** `llm/prompt_manager.py:65-86`

When cache TTL expires and Langfuse fetch fails, the hardcoded fallback constant is written with a fresh timestamp, discarding the previously-fetched valid prompt. During Langfuse instability, prompts revert to potentially stale hardcoded strings.

**LF-M2 — Duplicate Langfuse traces** `llm/client.py:281-300`

Two concurrent first-imports can both pass the `not in` check and append `"langfuse"` twice to `litellm.success_callback`. Every LLM call fires two traces.

**LF-M3 — API key frozen at import time** `llm/client.py:114-117`

`get_settings()` called at module level. Secrets rotated after process start are not picked up.

**LF-M4 — Langfuse monkey-patch swallows all init errors** `llm/client.py:254-277`

Bare `except Exception: pass` in the Langfuse 4.x shim silences any misconfiguration silently.

**LF-M5 — `invalidate_cache` misses non-standard labels** `llm/prompt_manager.py:102-108`

Only clears `production` and `latest` label variants. Any custom label (e.g. `"staging"`) is not cleared.

---

## Priority Fix Order

### Fix Today (crashes / completely broken features)

| Priority | ID | Fix |
|----------|----|-----|
| 1 | CRASH-1 | `pipeline/v1.py:463`: `[f.description for f in ...]` → `[f for f in ...]` (red_flags is `list[str]`) |
| 2 | CRASH-2 | `fit_score.py`: `(role.jd_text or "")[:8000]` |
| 3 | CRASH-3 | `supervisor/engine.py:292`: add `prompt_version=` param or give it a default |
| 4 | CRASH-4 | `webhooks_voice.py:1268` + evaluator: deserialize `paralinguistic` dict back to `EmotionFeatures` inside Arq job |
| 5 | V-M1 | `classifiers/voicemail.py:98`: change `a.get("answer", "")` → `a.get("answer_transcript", "")` |
| 6 | V-M3 | `voice_context.py:205-207`: fix key names (`current_ctc` → `current_ctc_lpa`, etc.) |
| 7 | PR-2 | `voice_screen_gen` Q5: guard `location` with `f"You're based in {loc}. " if loc else ""` |

### Fix This Week (data integrity / silent failures)

| Priority | ID | Fix |
|----------|----|-----|
| 8 | V-C2 | Add `claim_completion` CAS to recovery endpoint |
| 9 | V-C3 | Add `claim_completion` CAS to watchdog recovery path |
| 10 | V-C1 | Add orphaned-sentinel detector in watchdog: clear `_in_flight:*` values older than 15min |
| 11 | G-1 | Cancel history: inject synthetic cancelled pair into LLM history before building next turn |
| 12 | P-2 | `v1_evaluate_voice_call.py`: call `run_rejection` on REJECT path |
| 13 | P-3 | `offer.py`: call `set_stage(HIRED)` after offer email sends |
| 14 | ~~MS-C1~~ | ~~Add `UNIQUE(application_id, round)` constraint to `MeetingSession`~~ | **[FIXED]** auto_progress parks + nudges; book_meeting has Python-level idempotency |
| 15 | P-1 | Swap stage-set and email-send order in assignment and voice dispatch |

### Fix Next Sprint (quality / hallucination traps)

| Priority | ID | Fix |
|----------|----|-----|
| 16 | PR-3 | `voice_screen_gen` Q1/Q2: check `profile.projects` empty before instructing LLM to name one |
| 17 | PR-5–7 | `CEO_BRIEF_V1`: conditionally skip sections when data blobs are `{}` |
| 18 | G-2 | `edited_args` schema validation against tool parameter schema |
| 19 | MS-H2 | Fix Graph organiser email or use service account for free/busy lookup |
| 20 | MS-H3 | Re-validate `app.current_stage` inside `record_candidate_selection` before booking |
| 21 | LF-H1 | Add retry + backoff for Langfuse init; use shared Redis cache |
| 22 | P-4 | Wrap fit re-score + `auto_progress` in same transaction or use advisory lock |
| 23 | PR-11 | `MEETING_ANALYSIS_V1`: increase transcript budget or implement chunked summarisation |

---

## Latest Migration

```
alembic upgrade head
```
Current head: `0039_embedding_dim_256` (applied). Reduces pgvector embedding dimension from 1536 → 256 across all candidate profiles, with recreation of the ivfflat index.

---

## Fixes Applied (2026-06-18 – 2026-07-01)

| ID | Bug | Commit | Fix |
|----|-----|--------|-----|
| MS-C1 | 3-path auto-scheduler creates duplicate MeetingSessions | `8142683` | Disabled legacy auto-scheduler; `_fire_meeting` parks + nudges |
| P-12 | Duplicate MeetingSession rows on re-schedule | `a07c899` | `book_meeting` checks for existing active session before INSERT |
| GOOGLE_OAUTH_TOKEN_PATH | Env var set to token endpoint URL instead of file path | `.env` edit | Changed `https://oauth2.googleapis.com/token` → `secrets/google_token.json` |
| EMBED-1 | Embedding dimension 1536 → 256 | `0039_embedding_dim_256` | Reduced pgvector dim for all profiles; updated `litellm.aembedding()` calls with `dimensions=256` |
| REDACT-1 | Rejected candidates leak full profile in search | rejection redaction | `list_candidates`, `search_candidates`, `search_talent_pool` return only `{name, status: "rejected", reason}` via audit-log lookup |
| ORG-1 | Rebrand from GrabOn to InspireLabs | `0037_org_inspirelabs` | Updated org name, hiring persona, and seed org_context settings |
| PI-1 | Add PI assessment links to roles | `0038_role_pi_links` | Added `pi_cognitive_link`, `pi_personality_link` columns to roles table |

## New Bugs Spotted

| ID | File | Bug | Severity |
|----|------|-----|----------|
| NB-1 | `api/meetings.py:585` | `/agentic/technical/approve` calls `_kick_schedule_meeting(ceo)` which enqueues old `v1_schedule_meeting.schedule_meeting` worker — bypasses chat-driven book_meeting, so no idempotency check runs. Only affects admin dashboard approval path. | MEDIUM |
| NB-2 | `api/meetings.py:498-516` | `_kick_schedule_meeting` enqueues job `"schedule_meeting"` but the worker function `schedule_meeting` in `workers/jobs.py` calls `v1_schedule_meeting.schedule_meeting` — this is the OLD scheduling path. When the auto_progress was refactored, this backdoor was left open for admin-approved flow. | MEDIUM |
| NB-3 | `.env` line 141 had `GOOGLE_OAUTH_TOKEN_PATH` set to the token endpoint URL | Configuration error preventing OAuth token file from being found | FIXED |
| NB-4 | `db/base.py:380-450` | No `UNIQUE(application_id, round)` constraint on `meeting_sessions` table. Python-level check in `book_meeting` prevents dups, but a concurrent request or direct SQL insert can still create duplicates. | LOW |
| NB-5 | `activities/v1_evaluate_voice_call.py:259-273` | Voice eval `needs_hr_review` verdict parks stage at `NEEDS_HR_REVIEW` with no notification — sole notification path (supervisor event bus) is permanently broken (SV-C1). HR is never told to review; candidate sits silently until stall detector fires. | HIGH |
| NB-7 | `frontend/src/lib/useRecruiterChat.ts:101-125` | System messages (nudge cards, reschedule alerts) arrive via SSE and render in real-time, but `hydrate()` drops `role: "system"` on page reload — messages vanish forever. DB still has them, UI never renders them after refresh. | MEDIUM |
| NB-8 | `frontend/src/app/(app)/candidates/[id]/page.tsx:537-549` | `AssignmentFileArtifact.extracted_text_preview` field exists in the model (first ~2k chars) but is never surfaced in the UI. Recruiters see filename + size only — must download every file to evaluate coding assignments. | LOW |
| NB-9 | `backend/src/api/v1_dashboard.py:242-263` + `frontend/src/app/(app)/candidates/[id]/page.tsx` | Candidate detail endpoint returns ALL data (profile, screening, voice, meetings, assignment, audit) in one monolithic JSON payload. No lazy loading. 60K+ char frontend component loads everything upfront. | MEDIUM |
| NB-10 | `db/base.py:329-340` | `evidence_records.candidate_id` column has no FK constraint to `candidates.id`. Denormalized for query convenience but has no referential integrity. | LOW |
| NB-11 | `backend/src/api/v1_dashboard.py:150-200` | `/ceo/[id]` and `/hr/[id]` pages call the same `ceoDashboard.detail()` backend endpoint. Only the list page differs. Confusing for debugging. | LOW |
| NB-6 | `activities/v1_evaluate_voice_call.py:120-143`, `services/auto_progress.py:451`, `frontend/src/components/admin-review-panel.tsx:66` | `NEEDS_HR_REVIEW` is set by three unrelated code paths but `AdminReviewPanel` always renders the voice-screen "Push to assessment" panel. Post-meeting parking (auto_progress) and blank-transcript bail-out (evaluator) both land here — HR clicks "Push to assessment" and the assignment is re-sent to a candidate who is actually waiting for a meeting to be scheduled. | HIGH |
| NB-12 | `services/mail_ingest.py:211-300`, `services/imap_inbox.py:46-56` | Inbound email routing has no thread model. (a) Candidate replies are routed to the RETIRED supervisor bus (`publish_supervisor_event`) which no-ops when `enable_supervisor=False` (default) → every reply (meeting update, question, acceptance) is SILENTLY DROPPED. (b) `InboundMessage` captures `Message-ID` but NOT `In-Reply-To`/`References`, so a reply is matched only by `sender → most-recent active application` (`ORDER BY updated_at DESC LIMIT 1`) — wrong app when a candidate has 2 roles; and any reply whose subject starts with "application" is treated as a brand-new application (re-parsed from scratch, duplicate candidate/app). | HIGH |

### New Bug Details

**NB-1 — `/agentic/technical/approve` triggers old scheduling path, bypassing idempotent `book_meeting`** `api/meetings.py:585`

**Root cause:** `approve_technical` endpoint (line 585) calls `_kick_schedule_meeting(body.application_id, "ceo")` after recording the admin review. This helper enqueues an Arq job `"schedule_meeting"`, which dispatches `workers/jobs.schedule_meeting()` → `v1_schedule_meeting.schedule_meeting()` — the **original** scheduling code that pre-dates the chat-driven refactor. It calls `create_session()` unconditionally (no idempotency check).

Meanwhile, the new chat-driven `book_meeting()` in `chat_meeting.py` has the idempotency check (looks for existing active sessions before INSERT). But the admin-approved flow never reaches it.

**Impact:** If an admin approves the technical round via the dashboard (`POST /agentic/technical/approve`) and then the recruiter also schedules via Pulse (`schedule_meeting` tool), two `MeetingSession` rows are created for the same CEO round — one from the old job, one from `book_meeting`. The meetings tab shows duplicates.

**Fix:** Replace `_kick_schedule_meeting` → `"schedule_meeting"` job with a direct call to `chat_meeting.book_meeting()` inside `approve_technical()`. `book_meeting` requires `panel_emails` and `scheduled_at` — these need to be resolved from the role's panel config and slot availability, or the approval endpoint should pick a slot based on panel availability and pass it.

Alternatively: make `v1_schedule_meeting.schedule_meeting` idempotent with the same existing-session check, so both paths are safe.

---

**NB-2 — `_kick_schedule_meeting` splits into two competing paths, one of which has no fallback** `api/meetings.py:498-516`

**Root cause:** The helper function `_kick_schedule_meeting` has a dual-path design:

```python
async def _kick_schedule_meeting(application_id, round):
    queued = await enqueue("schedule_meeting", str(application_id), round=round)
    if queued:
        return  # Path A: job will run asynchronously
    # Path B: inline fallback (runs in HTTP request)
    try:
        await schedule_meeting(application_id=application_id, round=round)
    except Exception as exc:
        await log_audit(session, ..., action="schedule_meeting_failed", ...)
```

Path A (enqueue): Returns immediately. The Arq worker picks up the job later. If enqueue succeeds but the worker is down or errors, no retry — the audit log entry is never written.

Path B (inline): Calls `v1_schedule_meeting.schedule_meeting` directly. This runs inside the HTTP request lifespan. If it fails, the audit log entry is written. But this only runs when Arq is disabled (`.env` line 84: `ARQ_ENABLED=false`). In production (`ARQ_ENABLED=true`), only Path A runs.

Key problem: Path A has **no idempotency check** and **no audit trail on failure**. Both paths use the old `v1_schedule_meeting.schedule_meeting` which unconditionally creates a new `MeetingSession` row.

**Impact:** Every admin-driven technical approval creates a new CEO meeting session via the old path, bypassing the Python-level idempotency guard added to `book_meeting`. If the admin approves multiple times (intentional or not), multiple CEO meeting sessions accumulate. Duplicate calendar invites and bot dispatches.

**Fix:** Replace the `"schedule_meeting"` job handler to call `chat_meeting.book_meeting()` instead of `v1_schedule_meeting.schedule_meeting`. Or, merge the two paths into a single call to `book_meeting` with async fallback.

---

**NB-4 — No `UNIQUE` constraint on `(application_id, round)` in `meeting_sessions`** `db/base.py`

**Root cause:** The `meeting_sessions` table has no database-level unique constraint on `(application_id, round)`. While `book_meeting()` in `chat_meeting.py:267-278` has a Python-level check that prevents creating a duplicate when an active session exists, this check is:
1. Not atomic — a concurrent request between the SELECT and INSERT can slip through
2. Only applies to the chat-driven path — the old `v1_schedule_meeting` path and any direct `create_session()` calls bypass it entirely

**Impact:** Under concurrent scheduling requests (rare in practice for a single candidate), two sessions can be created for the same round. The meetings tab shows duplicates. Two calendar invites go out. Two bots join the same meeting.

**Fix:** Add a partial unique index or constraint to Alembic migration:
```sql
CREATE UNIQUE INDEX ix_meeting_sessions_app_round_active
ON meeting_sessions (application_id, round)
WHERE bot_status IN ('pending', 'scheduled', 'in_call');
```
This allows multiple historical/completed sessions per round (for reschedules) but prevents duplicates with active statuses. Then `create_session()` should catch the `IntegrityError` and handle gracefully.

---

**NB-5 — Voice eval `needs_hr_review` verdict is a silent dead end** `activities/v1_evaluate_voice_call.py:259-273`

`VoiceCallScore.verdict` is `Literal["clear_pass", "needs_hr_review", "clear_reject"]` — three valid values. When the LLM returns `needs_hr_review` (borderline call):

```python
elif score.verdict == "needs_hr_review":
    await set_stage(session, application.id, PipelineStage.NEEDS_HR_REVIEW, force=True)
    transition = "hr_review"
    await log_audit(...)
```

Then at the bottom of the function:
```python
# needs_hr_review: HR is notified via Teams/email through the supervisor event
# bus; no auto-progress, no rejection email.
return score
```

The comment describes the intended design — supervisor event bus fires a notification. But the supervisor engine has **never worked** (SV-C1: `complete()` raises `TypeError` on every call). The notification never fires. The candidate parks at `NEEDS_HR_REVIEW` with zero external signal to HR.

**Also affected:** The blank-transcript bail-out path (lines 120-143) ALSO sets `NEEDS_HR_REVIEW` and raises a `ValueError` — the Arq job fails, nothing is retried, HR is never told the call was too short.

**Impact:** Any borderline voice call or failed transcript is invisible until either the stall detector fires (15-min loop, if configured) or a recruiter manually navigates to the candidate detail page.

**Fix:** Replace supervisor notification with a direct Teams/email alert at the `transition == "hr_review"` branch — same pattern as `notify_round_complete` in `v1_meeting_analysis.py`. The blank-transcript path should also send a separate "voice call failed — retry needed" alert rather than relying on the supervisor.

---

**NB-6 — `NEEDS_HR_REVIEW` stage is overloaded; `AdminReviewPanel` always shows the wrong action for two of the three callers** `activities/v1_evaluate_voice_call.py:120`, `services/auto_progress.py:451`, `frontend/src/components/admin-review-panel.tsx:66`

`NEEDS_HR_REVIEW` is set in three distinct situations:

| Caller | Why | What HR should do |
|--------|-----|-------------------|
| `v1_evaluate_voice_call.py:259` | Borderline voice verdict | Review transcript → push to assignment or reject |
| `v1_evaluate_voice_call.py:120` | Transcript too short / blank | Retry voice call or manually advance |
| `auto_progress._fire_meeting()` | Meeting scheduling parked for Pulse chat | Open Pulse, type `schedule [round] meeting` |

`AdminReviewPanel` has a single branch for this stage:

```typescript
if (currentStage === "needs_hr_review") {
  return <Panel title="Voice screen — HR review needed">
    <Button onClick={() => submit("/agentic/voice-screen/promote")} />  // Push to assessment
  </Panel>
}
```

When a candidate is parked at `NEEDS_HR_REVIEW` because a meeting needs to be scheduled (case 3 — the most common path after the auto-scheduler was disabled), HR sees "Voice screen — HR review needed" and clicks "Push to assessment". This calls `/agentic/voice-screen/promote`, which sets `ASSESSMENT_EVALUATED` and triggers `auto_progress` → `_fire_assessment` → **assignment email is re-sent to the candidate**.

The frontend has no way to distinguish the three callers because the audit log (which records the action name) is not exposed to the admin panel component.

**Fix (backend):** Use distinct stages for the three cases:
- `NEEDS_HR_REVIEW` — keep for voice-screen borderline verdict only
- Add `VOICE_CALL_FAILED` (or reuse existing) for blank-transcript bail-out
- `MEETING_SCHEDULING_PENDING` (or use round-specific stages like `TECHNICAL_SCHEDULING_PENDING`) for meeting parking

**Fix (frontend, short-term):** Expose the last audit action for the `needs_hr_review` stage via the dashboard API so `AdminReviewPanel` can render context-appropriate UI without a backend stage refactor.

---

**NB-7 — System messages (nudge cards) lost on page reload** `frontend/src/lib/useRecruiterChat.ts:101-125`

**Root cause:** `hydrate()` function in `useRecruiterChat.ts` only processes `user`, `assistant`, and `tool` roles from the database. Messages with `role: "system"` are silently dropped. When the recruiter nudge worker creates a system message with a reschedule-request attachment card:

1. Nudge worker picks up `meeting_reschedule_requested` event → persists system message to DB → publishes to `recruiter-chat:{id}:nudge` Redis channel
2. SSE handler picks up nudge → yields `{type: "attachment", _nudge: true}` to browser
3. Frontend creates a `role: "system"` message with attachment → renders in real-time ✓
4. Page reload → `hydrate()` iterates DB messages → skips `role: "system"` → nudge card is **gone forever**

**Impact:** Recruiters see the reschedule request in real-time, but it disappears on refresh. The message is persisted in the DB but never rendered again. Creates distrust and missed notifications.

**Fix:** Add a `"system"` case to `hydrate()`:
```typescript
} else if (m.role === "system") {
  out.push({
    id: `srv-${m.sequence}`,
    role: "system",
    content: m.content || "",
    attachments: m.attachments || undefined,
    createdAt: new Date(m.created_at).getTime(),
  });
}
```

---

**NB-8 — Assignment file content invisible to recruiters** `frontend/src/app/(app)/candidates/[id]/page.tsx:537-549`

**Root cause:** The `AssignmentFileArtifact` Pydantic model (`v1.py:292`) has an `extracted_text_preview: str | None` field containing the first ~2k characters of each submitted file. But the frontend `AssignmentSection` component only renders `filename` and `size_bytes` — the preview text is never fetched or displayed.

**Impact:** Recruiters must download each file to evaluate coding assignments. No syntax highlighting, no inline preview. The `extracted_text_preview` data exists on the backend (stored in `assignment_submission.parse_result`) but is wasted.

**Fix:** Pass `extracted_text_preview` from `AssignmentFileArtifact` to the frontend API response. Render code files (`.py`, `.js`, `.tsx`, `.ts`, `.jsx`) with syntax highlighting. Add a collapsible "Show file content" toggle per file.

---

**NB-9 — Candidate detail endpoint is a monolithic bottleneck** `backend/src/api/v1_dashboard.py:242-263`, `frontend/src/app/(app)/candidates/[id]/page.tsx`

**Root cause:** `/dashboard/v1/candidates/{id}` returns a single JSON response joining across 6+ tables:
- `candidate`, `role`, `profile`, `audit` (full timeline), `screening_questions`, `screening_evaluation`, `assignment_submission`, `voice_evaluation`, `meeting_reports`, `fit_breakdown`, `fit_score`, `fit_tier`, `journey_report`, `admin_review`, `application_mail`, `resume_download_url`

The frontend renders all of this in a single 60K+ character page component. No lazy loading — all sections load even if the recruiter never scrolls to see them.

Meanwhile, standalone `/meetings`, `/assessments`, and `/voice-screens` pages are cross-candidate list views that serve different purposes. But the candidate detail page duplicates all their data.

**Impact:** Slow page loads. Increasing payload with every new feature. No cache granularity — mutating one section re-fetches everything. Frontend rendering complexity.

**Fix:** Split backend into sub-resources: `/candidates/{id}/voice`, `/candidates/{id}/meetings`, `/candidates/{id}/assignment`, `/candidates/{id}/audit`. On frontend, lazy-load sections below the fold or behind collapsible headers. Add `?include=section1,section2` query parameter.

---

**NB-10 — `evidence_records.candidate_id` lacks FK constraint** `db/base.py:329-340`

**Root cause:** The `evidence_records` table has `candidate_id: UUID NOT NULL` but no `ForeignKey` to `candidates.id`. It's denormalized from `application_id → candidates.id` for query convenience (`ix_evidence_candidate_fact` index queries by candidate_id directly).

**Impact:** If a candidate row is deleted, evidence records orphaned. No cascade. No referential integrity.

**Fix:** Add FK constraint: `candidate_id → candidates.id ON DELETE CASCADE`. The column already has matching UUID type and values; migration should be safe.

---

**NB-11 — `/ceo/[id]` and `/hr/[id]` share the same backend endpoint** `backend/src/api/v1_dashboard.py:150-200`, `ceo_dashboard.py`

**Root cause:** Both `ceo_dashboard` and `hr_dashboard` routers call `ceoDashboard.detail()` — the same endpoint. The only difference is the list page that links to them (`/ceo` shows candidates at CEO stage, `/hr` shows candidates at HR stage).

**Impact:** Confusing for debugging. If the CEO detail endpoint breaks, both pages break. The frontend `AgenticJourney` component is shared but the route distinction is meaningless at the data level.

**Fix:** Either alias `/hr/[id]` to redirect to `/ceo/[id]`, or give each its own dedicated endpoint with proper naming.


---

**NB-12 — Inbound email has no thread/conversation model; replies are dropped or misrouted** `services/mail_ingest.py:211-300`, `services/imap_inbox.py:46-56`

**Root cause.** Mail ingestion treats every message in isolation. Two compounding problems:

1. **Replies hit the dead supervisor bus.** A message whose subject does NOT start with `"application"` is assumed to be a candidate reply and routed through `_try_emit_candidate_email_event → publish_supervisor_event(...)`. But the supervisor was retired; `typed_event_bus.publish_event` returns `None` immediately when `enable_supervisor=False` (the default). So meeting-update replies, candidate questions, document re-sends, and acceptances are **silently dropped** — no durable event, no inbox item, no action. (Same failure class as NB-5, but on the email path.)

2. **No threading headers, brittle matching.** `InboundMessage` captures `Message-ID` but not `In-Reply-To` / `References`. A reply is bound to an application purely by `sender email → most recent active application` (`ORDER BY updated_at DESC LIMIT 1`) plus a subject-prefix string test. Consequences:
   - A candidate applying to **two roles** → their reply attaches to whichever application was touched last, often the wrong one.
   - A reply whose subject happens to start with `"application"` → misclassified as a **brand-new application**, re-parsed from scratch (duplicate candidate/application rows, possible second voice call / assignment).
   - A meeting-update reply that re-attaches the resume can spawn a fresh intake.

   Dedup itself is fine (`processed_messages` keyed by `Message-ID` prevents processing the *same* email twice, survives restarts) — the gap is that there is no link from a reply to the *correct* application/thread.

**Impact.** In real inboxes (multiple conversations per candidate: new application, screening reply, meeting reschedule, offer acceptance), the system either loses the reply entirely or files it against the wrong role. HR never sees the candidate's response.

**Fix.**
1. Capture `In-Reply-To` and `References` headers in `InboundMessage` (and store outbound `Message-ID`s / a `thread_id` on the application when we send mail).
2. Resolve a reply to the **exact** application by thread reference first; fall back to sender+role, never blind "most recent".
3. Route replies to the **durable `domain_events`** backbone — `emit_event(type=CANDIDATE_EMAIL_REPLY, requires_action=<intent-dependent>, ...)` — so they land in the HR inbox instead of the dead supervisor bus.
4. A reply must NEVER re-enter `run_intake`; only a genuinely new thread (no matching candidate/thread) starts a fresh application.

> Related: the LLM application-authenticity gate (`CLASSIFY_EMAIL_V1`) is currently
> bypassed — the live path uses only a subject-prefix + role-token match (see the
> Progress Tracker note). Reviving it as a real stage is part of intake hardening.


---

## Progress Tracker

> Backend-only phase (UI deferred). Validation bar in this env: `python -m
> compileall src` (clean) + targeted pytest on dependency-light modules
> (`test_pipeline_engine` 23, `test_model_registry` 5). Heavy deps (sqlalchemy,
> litellm, temporalio) aren't installed locally, so full integration tests run in
> CI/prod, not here.

### DONE

**Stage 1 — role-tuned scoring + foundation**
- Replaced hardcoded GrabOn/"builder" culture in every scoring prompt with role
  `company_context` + `evaluation_spec` via `services/scoring_context.py`
  (fit v4, screening gen v3/eval v4, voice gen v5/eval v5, gemini_audio_eval,
  assignment_parse v3, meeting_analysis v2). Producer = recruiter-artifacts
  `propose_role_draft`; migration 0033 added `roles.company_context`.
- Keystone: `set_stage()` dual-writes `current_stage_key`/`stage_status` + emits a
  durable `stage_changed` domain_event (legacy `current_stage` still authoritative).
- Supervisor RETIRED (engine + typed_event_bus marked `[SCRAPE/RETIRED]`, lifespan
  task disabled). Dead Temporal/Chat-V2 code `[SCRAPE]`-marked.

**Stage 2 — single progression engine**
- `services/pipeline_engine.py`: pure planner over `role_pipeline_stages`
  (intake/parse/fit inline → fire auto stages → park manual gates each with a
  DISTINCT action: review_assessment / schedule_interview / hire_or_reject). Kills
  the overloaded `NEEDS_HR_REVIEW` (root of the "schedule HR round re-sends
  assignment" bug, NB-6 backend half). 23 unit tests.
- `auto_progress` rewritten as the IO shell over the engine; deleted the legacy +
  pipeline_template branches. `DEFAULT_PIPELINE` fixed to the real flow (no bogus
  written-screening stage; voice IS the screen).
- Approval-gate backdoor killed (NB-1/NB-2): `_kick_schedule_meeting` now parks +
  emits `schedule_interview` instead of the duplicate-prone legacy `schedule_meeting`
  job. Human still books via the idempotent chat `book_meeting` (unchanged).

**SP0 crashes + auto-lane trust** (`fix/01-sp0-stabilize.md`)
- CRASH-1 (red_flags `.description`), CRASH-2 (null `jd_text` guard + park),
  CRASH-3 (supervisor retired), CRASH-4 (gone — moved to Gemini), V-M1 (voicemail
  key), V-M3 (already correct), PR-2 (voice "based in None" guarded).
- P-2 (voice rejection email — `run_rejection` on reject), P-3 (offer → HIRED),
  NB-5 (needs_hr_review / blank-transcript now emit durable `requires_action`
  domain_events instead of the dead supervisor bus).
- Dead emotion module marked `[TO BE REMOVED]` (emotion_client + EmotionFeatures);
  `candidate_emotion_timeline` kept (live in meeting analysis).
- Voice webhook idempotency guard (V-C1/2/3, V-M5): `processing_status` CAS on
  voice_calls across webhook + /recover + watchdog; stale-claim sweep; provider id
  UNIQUE. Migration 0034.
- Generic stage-runner (fix/10, P-0): `services/stage_runner.advance_candidate`
  + per-stage overlay `applications.stage_results` (migration 0036). Evaluators
  now record a verdict (pending|on_going|pass|fail) and let the engine pick the
  next stage from `role_pipeline_stages` -- NO hardcoded tech->ceo->hr->offer.
  Cut over: pipeline/v1 (post-fit, post-screening, assignment tail),
  v1_meeting_analysis (mode-aware: auto acts, manual parks for approval),
  v1_evaluate_voice_call, api/meetings approval gates, v1_dashboard tech/ceo
  decisions. email_filter + fit are real stage types; a 1-stage `[email_filter]`
  JD runs. Legacy current_stage kept as a back-compat projection. 52 unit tests
  (incl. 1-step / no-CEO / reorder configs).
- Inbound-email funnel (NB-12 + authenticity): pure `services/email_filter`
  (reply/internal/application/ignore), unit-tested (16). IMAP + Graph capture
  thread headers; replies thread-match to the exact application and route to
  durable `domain_events`; org domains live in `organizations.settings`
  (`get_email_domains`/`set_email_domains`, seed migration 0035);
  `create_application` now stamps `org_id` (default org) so durable events are
  org-scoped. Graph new-applicant ingest deferred to IMAP (no double-intake).

**Per-stage model registry** (user request)
- `src/llm/model_registry.py`: `STAGE_MODELS: dict[Stage, model_id]` +
  `model_for(stage)`. Replaced ALL `client.smart`/`client.fast`/`llm_model_*` call
  sites (~24) with explicit `model_for(Stage.X)`. Cheap work → gpt-4o-mini, heavy
  judgment → gpt-4.1-mini. 5 unit tests. (Only the retired supervisor still uses the
  old setting, intentionally.)

### PENDING (backend)

| Priority | Item | Notes |
|---|---|---|
| ~~HIGH~~ DONE | ~~**NB-12** email threading~~ | **FIXED 2026-06-22**: IMAP + Graph capture In-Reply-To/References; replies thread-match to the exact application via `email_sends.provider_message_id` (fallback: sender's most-recent active app), routed to durable `domain_events` (`candidate_email_reply`, requires_action) instead of the dead supervisor bus; replies never re-intaken. |
| ~~HIGH~~ DONE | ~~Revive **email authenticity gate**~~ | **FIXED 2026-06-22 (deterministic)**: replaced the `startswith("application")` gate with `services/email_filter.classify_inbound` funnel -- reply / internal (org-domain) / application / ignore. Application heuristic = resume attachment + subject signal, or strong subject alone. 100% deterministic per user; the `CLASSIFY_EMAIL_V1` LLM gate stays dormant as a future borderline tiebreaker. |
| ~~MED~~ DONE | ~~**P-1** stage-set-before-side-effect~~ | **FIXED 2026-06-22**: All 3 dispatchers now use claim_stage_processing -> do side-effect -> record_stage_verdict/mark_stage_failed lifecycle. assignment: claim before send, verdict on_going after email succeeds, mark_failed on error. voice_screening: claim before set_stage, verdict on_going after provider.create_call, mark_stage_failed on error. schedule_meeting: claim before set_stage, verdict on_going after meeting booked + invites sent. |
| MED (reduced) | Graph webhook new applicants | Now runs the shared funnel; durably routes REPLIES (NB-12). New-applicant *ingestion* from Graph still intentionally deferred to the IMAP poller (the floor that also pulls attachments) -- so no double-intake. Wire Graph-side new-applicant ingest only if push-latency matters. |
| ~~MED~~ DONE | ~~Voice webhook CAS~~ | **FIXED 2026-06-22**: voice_calls.processing_status CAS guard (claim_processing) across webhook + /recover + watchdog; watchdog sweeps stale claims (V-C1); partial UNIQUE(provider, provider_call_id) (V-M5). Migration 0034. |
| ~~LOW~~ DONE | ~~Retire legacy `pipeline_template` JSONB~~ | **FIXED 2026-06-22**: `create_role` tool + roles API create endpoint now seed `role_pipeline_stages` rows (custom template mapped to stages, or seed_default). `pipeline_templates.py` marked DEPRECATED (retained for preset lookup/validation). Column kept for back-compat reads. |
| ~~LOW~~ DONE | ~~Langfuse resilience~~ | **FIXED 2026-06-22**: LF-H1 retry-with-backoff init (60s/300s cooldown). LF-M1 stale-while-revalidate (keeps last good value on fetch failure). LF-H2 TTL reduced 60s->30s (full fix needs Redis). LF-M2 set-based callback dedup. LF-M4 shim logs at debug. LF-M5 invalidate_cache prefix-match. |
| ~~LOW~~ DONE | ~~CEO-brief hallucination guards~~ | **FIXED 2026-06-22**: PR-4..7 -- generator passes DATA_NOT_AVAILABLE for empty/null data blobs. Prompt v2: guard instruction to omit sections with unavailable data. Removed hardcoded GrabOn company culture; sections now conditional. |

### PENDING (frontend — explicitly deferred per user)
- Show role **company persona/context** in the JD-creation UI (data on
  `roles.company_context`).
- Candidate workspace: split the monolith endpoint into sub-resources (NB-9),
  surface buried data (assignment preview NB-8, recordings, eval reasoning),
  `hydrate()` system-message fix (NB-7), kill `/ceo/[id]`+`/hr/[id]` dupe (NB-11),
  AdminReviewPanel relabel (NB-6 frontend half), the new ArtifactPanel flow.
