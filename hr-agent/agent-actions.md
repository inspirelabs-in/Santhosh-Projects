# Agent Actions Log

Running, timestamped log of changes the agent made. Newest entries appended at
the bottom. Format: `<YYYY-MM-DD HH:MM TZ> : <action>`.

---

2026-06-19 (earlier, planning) : Read the audit set (`architecture.md`, `data.md`,
`meeting-scheduling.md`, `prompts.md`) and produced the revamp plan under `/fix`
(`README.md`, `00-BUILD-INDEX.md`, `01`–`08`). Locked decisions: single-org +
tenant-ready schema; Split Inbox + Pulse home; aggressive-but-expand-contract
rebuild; auto-up-to-assessment / human-in-the-loop after; derived inbox over a
durable event log; relational per-role pipeline; persona-driven evaluation;
backend-first, auth deferred. Defined the Phase-0 contract + the 60-40 module
split (A = pipeline+evaluation engine, B = work surface + data APIs).

2026-06-19 22:22 IST : SCHEMA (Phase 0) — Pydantic type contract added:
`backend/src/models/pipeline.py` (StageType/StageMode/StageStatus, StageEvalSpec,
PipelineStageConfig, PipelineStageDef, DEFAULT_PIPELINE), `models/evaluation.py`
(EvaluationDimension/Knockout/EvaluationSpec, weights validated ~100),
`models/persona.py` (HiringPersona), `models/events.py` (EventType/ActionType/
NotificationType).

2026-06-19 22:22 IST : SCHEMA (Phase 0) — ORM in `backend/src/db/base.py`:
added nullable `org_id` to candidates/roles/applications/panel_members;
`roles.evaluation_spec` + `roles.pipeline_stages` relationship;
`applications.current_stage_key` + `stage_status`; `meeting_sessions.stage_key`;
new tables `organizations` (hiring_persona), `users`, `role_pipeline_stages`,
`domain_events`, `action_overlay`, `notifications`. All additive; legacy columns
(`current_stage`, `round`, `pipeline_template`, `scoring_rubric`) left untouched
and authoritative.

2026-06-19 22:22 IST : SCHEMA (Phase 0) — migration
`db/migrations/versions/0031_phase0_foundation.py` (chains off `0030`): creates
the new tables/columns/indexes; partial-unique meeting index scoped to
`stage_key IS NOT NULL`; `evidence_records.candidate_id` FK added `NOT VALID`;
backfill seeds one GrabOn org, stamps `org_id`, seeds the default 12-stage
pipeline per existing role, best-effort maps legacy `current_stage` →
`current_stage_key`/`stage_status`. Verified with `py_compile` (no live
mapper/migration run — needs Docker deps).

2026-06-19 22:22 IST : TIER 1 (Phase-0 glue) — repositories added under
`db/repositories/`: `organization.py`, `domain_event.py`,
`role_pipeline_stage.py` (incl. `seed_default` + `next_stage`),
`action_overlay.py`, `notification.py`.

2026-06-19 22:22 IST : TIER 1 — event infra `db/events.py`
(`emit_event`/`resolve_event`; persists a domain event + best-effort Redis push
via existing `services.events.publish_event`).

2026-06-19 22:22 IST : TIER 1 — state machine `services/state_machine.py`
(`transition()` = single writer of `current_stage_key`/`stage_status`, validates
against the role pipeline, emits `stage_changed`; `advance()` = move to next
enabled stage). All Tier-1 files verified with `py_compile`. Phase 0 contract
complete and ready to freeze.

2026-06-19 22:22 IST : CHAT AGENT (scan) — ran two read-only Explore agents over
the Pulse module. Backend: confirm cards are JSONB attachments on
`recruiter_messages` (no artifacts table); `create_role_with_assignment` sets
`pipeline_template` JSONB but not `role_pipeline_stages`/`scoring_rubric`/
`evaluation_spec`; system prompt currently says "ask exactly one question".
Frontend: editable forms already exist inside `ConfirmCard.tsx`; no artifact
panel; no user/agent id rendered (the "ids" = the confirm card's raw-JSON
drawer). Single-column inline stream.

2026-06-19 22:22 IST : CHAT AGENT (decisions) — artifacts = structured editable
output, agent- and human-editable; questioning rewritten to gather full context
in 3-4 turns then generate; scope = end-to-end backend + DB, minimal UI (remove
the old confirm box + raw-JSON drawer, add an artifact icon top-right, auto-open
the panel on a streamed artifact event, render editable form).

2026-06-19 22:22 IST : CHAT AGENT (artifacts data foundation) — added
`models/artifacts.py` (ArtifactType/ArtifactStatus, RoleDraftContent composed
from PipelineStageDef + EvaluationSpec, Artifact wire model); ORM
`RecruiterArtifact` in `db/base.py`; migration `0032_recruiter_artifacts.py`
(chains off 0031); repository `db/repositories/artifact.py` (create / get /
get_active_for_conversation / update_content[+version bump] / set_status).
Verified with `py_compile`. NEXT: agent tool to write/update the artifact, the
artifact SSE event, GET/PATCH/apply endpoints, prompt rewrite, then minimal FE.

2026-06-19 22:22 IST : CHAT AGENT (backend, end-to-end) — wired artifacts through
the agent + API. `recruiter_agent/tools.py`: new ungated `propose_role_draft`
tool (validates RoleDraftContent, returns normalized content) + registered in
TOOLS. `schemas.py`: `propose_role_draft` tool def. `runner.py`: artifact branch
in the direct-exec path — upserts the conversation's active artifact (create or
update_content) and yields a new `{"type":"artifact"}` SSE event (passes through
the existing `_sse_loop` passthrough at recruiter_chat.py:503-506, no whitelist
change). `prompts.py`: rewritten to v4-pulse-artifacts — interview the user over
3-4 conversational turns (role, experience, ideal-candidate, must-haves,
deal-breakers, comp, rounds), derive ROLE-SPECIFIC evaluation dimensions (no
"builder mindset" defaults), then call propose_role_draft; revise by calling it
again. `api/recruiter_chat.py`: endpoints GET
`/conversations/{id}/artifact` (active), PATCH `/artifacts/{id}` (human edit +
version bump), POST `/artifacts/{id}/apply` (creates Role + saves
evaluation_spec + seeds role_pipeline_stages from the draft + generates the
assignment + marks artifact applied). All verified with `py_compile`. NEXT
(only remaining slice): minimal FE — remove the inline confirm box + raw-JSON
drawer, add the artifact file icon top-right, auto-open the panel on the
`artifact` event, render the editable form, wire PATCH save + apply.

2026-06-19 22:22 IST : CHAT AGENT (frontend, minimal) — artifact side panel end
to end. `lib/useRecruiterChat.ts`: added `ArtifactData` + `activeArtifact` /
`artifactOpen` state, a new `artifact` SSE case (auto-opens the panel),
`fetchActiveArtifact` (loads any existing draft on conversation open, behind the
icon, no auto-open on reload), `saveArtifact` (PATCH) + `applyArtifact` (POST),
and open/close; suppressed the `propose_role_draft` tool row (skip in `tool_call`
+ in `hydrate`) and the inline `artifact` attachment so nothing renders as a
block. New `components/recruiter-chat/ArtifactPanel.tsx`: self-contained editable
form for a role_draft (title, CTC, location, JD, pipeline stages add/remove/mode,
evaluation dimensions add/remove + weight sum check, assignment config) with Save
+ Apply; re-seeds on artifact id/version change so agent rewrites refresh it.
`app/(app)/dashboard/page.tsx`: added the artifact file icon top-right (shows when
a draft exists, toggles the panel) + split the body into chat column + panel
aside. `components/recruiter-chat/ConfirmCard.tsx`: removed the raw-JSON drawer
(the "ids") + its `showRaw` state + now-unused ChevronUp/Down imports. NOT type-
checked locally (no node_modules; frontend deps run in Docker) — edits were
surgical and reviewed. Backend verified with py_compile throughout.

2026-06-19 22:22 IST : CHAT AGENT — module complete end to end (backend + minimal
FE). To run: apply migrations (entrypoint runs `alembic upgrade head`, i.e. 0031
+ 0032) and rebuild the frontend. Flow: Pulse interviews -> propose_role_draft ->
artifact event auto-opens the editable panel -> human/agent edits -> Apply
creates the Role (+ evaluation_spec + pipeline stages + assignment).

2026-06-20 : RUNTIME WIRING (increment 1 of 3) — company context generated at JD
stage. New `models/context.py` `RoleContext` (intensity light|standard|high|
critical + summary + what_matters_here + hiring_bar); added `company_context` to
`RoleDraftContent`; new nullable `roles.company_context` JSONB (ORM + migration
`0033_role_company_context`); `propose_role_draft` schema + the v4 prompt now
generate it (intensity scaled to the role's stakes); apply endpoint persists
`draft.company_context` onto the Role. Verified with `py_compile`. (FE: the panel
preserves company_context through Save via object spread but does not yet expose
an editor for it — deferred, backend-first per request.) NEXT: increment 2 =
rubric-driven scoring (local prompts read evaluation_spec + company_context);
increment 3 = dynamic flow (candidate runs on role_pipeline_stages via
transition()/reconciler, emitting domain_events) — needs an orchestration scan of
auto_progress + activities + the intake path first.

2026-06-20 : RUNTIME WIRING (scan) — ran 5 parallel Sonnet Explore agents over the
whole pipeline (orchestration core, inbound/parse/fit/screening, voice+assignment,
meetings+Read.ai+analytics, dead-code audit). Synthesized into
`fix/09-runtime-wiring.md`: the live chain, the cutover strategy (Stage 1 =
additive dual-write `stage_key` + emit `domain_events` + rubric-driven scoring via
local prompts + bug fixes; Stage 2 = flip `auto_progress` onto
`role_pipeline_stages` + re-point HR approval gates off the legacy scheduler), the
in-passing bug list (voicemail key, dead needs_hr_review verdict, `_fire_chat_screen`
ImportError landmine, Graph webhook drops new applicants), and the reconciled
`[SCRAPE]` map (SAFE-now vs NEEDS-DECISION incl. the supervisor retire call).
Awaiting checkpoint decisions before changing the live pipeline.

2026-06-20 : STAGE 1 (runtime wiring) — decisions: supervisor → mark [SCRAPE] +
comment out; sequence → Stage 1 then Stage 2. Built so far + verified (py_compile):
(1) KEYSTONE — `services/state_machine.py` got `legacy_stage_to_key()` + the
legacy→(stage_key,status) map; `db/repositories/v1_application.py:set_stage()` now
DUAL-WRITES `current_stage_key`/`stage_status` and emits a durable `stage_changed`
domain_event (best-effort, non-fatal; legacy `current_stage` + supervisor publish
left intact). (2) RUBRIC SCORING (core issue) — new `services/scoring_context.py`
`scoring_prompt_vars()`; `FIT_SCORE_V1` bumped to v4: replaced the hardcoded
"Builders over spectators" GrabOn block with `{company_context_json}` +
`{evaluation_spec_json}` (role-tuned, no generic builder bias); `activities/fit_score.py`
captures `role.evaluation_spec`/`company_context` and passes them. REMAINING STAGE 1
(same recipe): screening_gen, screening_eval, voice gen, voice eval (text +
gemini_audio_eval), assignment_parse, meeting_analysis; bug fixes (voicemail key,
dead needs_hr_review verdict, `_fire_chat_screen` ImportError guard); [SCRAPE] marks
(Chat-V2 agent/* orphans, score_responses/report/classify/schedule.run_book_interview,
calendar_sync create/cancel, commented auto-scheduler) + supervisor mark+comment+
disable lifespan. THEN Stage 2 (progression flip).

2026-06-20 : STAGE 1 COMPLETE (verified: full `compileall src` EXIT 0 + a render
test that injects a sample coupon-editor spec/context into every prompt and asserts
the GrabOn block is gone + the new vars substitute). Picked up from the keystone +
fit_score and finished the rest:
(A) RUBRIC SCORING wired into all remaining touchpoints via `scoring_prompt_vars()`
— prompts now carry `{company_context_json}`+`{evaluation_spec_json}` and the
hardcoded GrabOn/builder culture was removed from: screening_gen (v3), screening_eval
(v4), voice_screen gen (v5) + eval (v5), gemini_audio_eval `_EVAL_PROMPT`,
assignment_parse (v3), meeting_analysis (v2, also swapped `scoring_rubric` → primary
`evaluation_spec` w/ rubric kept as supplement). Activities pass
`role.evaluation_spec`/`company_context`: v1_generate_screening, v1_evaluate_screening,
v1_voice_screening (gen), v1_evaluate_voice_call (text + gemini call args),
v1_parse_assignment, v1_meeting_analysis (snapshotted inside session).
(B) BUG FIXES: voicemail classifier now reads `answer_transcript` (w/ `answer`
fallback) in all 3 spots — detection was a silent no-op; added `needs_hr_review` as a
3rd verdict tier to BOTH voice eval paths (text VOICE_SCREEN_EVAL_V1 + gemini
`_EVAL_PROMPT`) so the evaluator's park branch is no longer dead;
`auto_progress._fire_chat_screen` no longer imports the retired `run_apply_to_chat`
(guarded no-op → parks instead of ImportError).
(C) [SCRAPE] MARKS (non-destructive, header/symbol comments only): whole-file dead
— activities/score_responses, report, classify; symbol-level — schedule.run_book_interview
/book_interview_activity (KEEP run_propose_slots), calendar_sync.create_event/cancel_event
(KEEP find_free_slots), agent/generators.gen_tailored_questions+extract_turn (KEEP
gen_assignment), agent/schemas Chat-V2 models (KEEP AssignmentBriefOut+), agent/prompts
tailored_qs+extract. Verified no live importers before marking.
(D) SUPERVISOR RETIRED: marked supervisor/engine + services/typed_event_bus
[SCRAPE/RETIRED] (engine never ran: enable_supervisor=False default + crash;
domain_events supersedes it), and DISABLED the lifespan task in api/main.py (import +
create_task commented, removed from `_all_tasks`). DELETED the unreachable commented
LEGACY AUTO-SCHEDULER block (47 lines) in auto_progress.py.
NEXT: Stage 2 (flip auto_progress source-of-truth onto role_pipeline_stages +
re-point HR approval gates off the legacy scheduler). Graph-webhook-drops-new-applicants
still noted for intake hardening.

<!-- Append new entries below this line -->

2026-06-21 : STAGE 2 (progression flip) — IN PROGRESS. Confirmed the ideal flow
with the user: each JD fixes an ordered stage list + per-stage mode (auto|manual);
the engine just maps the candidate's current stage to the next one. Root cause of
the live bugs (esp. "schedule HR round re-sends the assignment", NB-6): 3
progression engines fighting (legacy current_stage chain + pipeline_template JSONB
+ unused role_pipeline_stages) and one OVERLOADED `needs_hr_review` set by 3
callers. Stage 2 collapses all into the relational pipeline.
DONE + VERIFIED so far:
(1) NEW PURE ENGINE — `services/pipeline_engine.py`: DB-free planner
`plan_transition(stages, current_stage_key) -> Plan(action, stage)`. Walks enabled
`role_pipeline_stages` by position; skips inline (intake/parse/fit); fires auto
stages (screening/voice_screen/assignment/offer); parks manual gates with a
DISTINCT action each — assessment_review→PARK_REVIEW, interview→PARK_SCHEDULE
(always, even if marked auto: a human runs it), decision→PARK_DECISION; auto-mode
assessment_review/decision are auto-advanced; manual "fire" stage→PARK_MANUAL.
(2) UNIT TEST — `tests/unit/test_pipeline_engine.py`, 23 tests, ALL PASS (installed
pytest+email-validator locally; the suite's other modules need heavy deps so
py_compile/compileall stays the broad bar). Explicitly locks the HR-round bug:
`hr` interview → `decision`, asserts it is NOT FIRE_ASSIGNMENT. Also covers
drop/add/reorder stages + mode flips.
(3) DEFAULT_PIPELINE fixed to the ideal flow — removed the bogus `screening` stage
(voice IS the screen); now intake→parse→fit→voice_screen→assignment→
assessment_review→technical→ceo→hr→decision→offer.
(4) auto_progress REWRITTEN as the IO shell over the engine: resolves
current_stage_key (dual-written by set_stage; legacy fallback), runs the planner,
fires the activity or `_park_gate()` (writes stage_key+status + a distinct
`requires_action` domain_event: review_assessment/schedule_interview/hire_or_reject).
Deleted the legacy `_template_progress`/`_legacy_progress`/`_fire_step`/
`_fire_meeting`/`_fire_chat_screen`/`_fire_manual_step` + 2 dead helpers; added
`_fire_screening`/`_fire_offer`; kept voice side-effects (confirmation/joining
calls) via `_maybe_fire_voice_side_effects`. `compileall src` CLEAN.
NEXT (Stage 2 remainder): re-point HR approval gates (/agentic/assessment/review,
/technical/approve, /ceo/approve) off the legacy `_kick_schedule_meeting` →
`schedule_meeting` backdoor (NB-1/NB-2) to park+notify; then delegate standalone
bug fixes to sub-agents (offer→HIRED P-3, voice rejection email P-2, HR-review
notification NB-5). Graph-webhook-drops-new-applicants still noted.

2026-06-21 (cont) : STAGE 2 REMAINDER — DONE + VERIFIED (`compileall src` CLEAN +
23/23 engine tests pass).
(5) APPROVAL-GATE BACKDOOR KILLED (NB-1/NB-2) — `api/meetings.py`
`_kick_schedule_meeting` no longer enqueues the legacy `schedule_meeting` job
(which created duplicate MeetingSession rows + bypassed idempotent chat booking).
It now PARKS the candidate at the next interview stage_key (status=scheduled) and
emits a distinct `schedule_interview` requires_action event + realtime nudge. The
human still books via the existing chat-driven `book_meeting` (confirmed intact +
idempotent) — booking method unchanged, only the buggy auto-trigger removed. The
`/assessment/review`, `/technical/approve`, `/ceo/approve` gates now route through
this. `ai_schedule_meeting` (explicit opt-in agent scheduling) left as-is.
(6) STANDALONE BUG FIXES:
 - P-3 (offer→HIRED): `activities/offer.py` now `set_stage(HIRED, force=True)` inside
   the decision session when the offer email sent. (sub-agent, verified)
 - P-2 (voice rejection email): already fixed in-tree — `v1_evaluate_voice_call.py`
   calls `run_rejection(RejectionInput(...))` whenever `transition=="rejected"`
   (covers clear_reject verdict + fit-rescore-RED). No change needed.
 - NB-5 (HR-review silent dead-end): `v1_evaluate_voice_call.py` now emits durable
   `requires_action` domain_events (the inbox backbone that replaced the dead
   supervisor bus): needs_hr_review verdict → actionable `voice_evaluated`;
   blank/short-transcript bail-out → actionable `voice_call_failed` (emitted in-
   session before the raise). Stale supervisor-bus comment fixed.
STAGE 2 COMPLETE. Backend now: single progression engine (role_pipeline_stages),
distinct gate actions (no overloaded needs_hr_review), HR always notified via
durable events. NOT TOUCHED (per user: backend-only for now): frontend/UI, the
candidate detail page split (NB-9), AdminReviewPanel relabel (NB-6 frontend half).
Remaining backend notes for later: Graph webhook drops new applicants (intake
hardening); the legacy `pipeline_template` JSONB + `pipeline_templates.py` registry
are now unused by progression (still used by roles API presets/validation — safe to
retire once the JD builder writes role_pipeline_stages directly).

2026-06-21 (cont) : SP0 CRASH + TRUST FIXES (per fix/01-sp0-stabilize.md). Verified
`compileall src` CLEAN + 23/23 engine tests pass.
- CRASH-1 (pipeline/v1.py): red_flags is list[str] -> removed the `.description`
  access (threw on EVERY non-clear_pass screening).
- CRASH-2 (fit_score.py): guarded `jd_text[:8000]` with `(... or "")` AND added an
  entry guard that PARKS the candidate (RED, knockout "role_jd_missing") when
  role.jd_text is empty, instead of scoring garbage / crashing.
- PR-2 (voice_screening Q7): location clause now CONDITIONAL -- Aria drops it
  (never says "based in None/n.a") when the role has no real location.
- CRASH-4 / V-M3: NOT NEEDED -- already resolved by the move to Gemini
  (webhooks_voice has zero paralinguistic/model_dump refs; voice_context.py already
  uses the correct *_lpa / *_days / current_location keys).
- Marked the dead emotion module [TO BE REMOVED]: services/emotion_client.py stub +
  models/v1.EmotionFeatures (orphaned since Gemini does emotion inline). KEPT
  candidate_emotion_timeline -- still used by the live MEETING analysis path.
SP0 status: CRASH-1/2/3/4, V-M1, V-M3, P-2, P-3, NB-5, PR-2 all DONE.
REMAINING SP0 (lower priority): P-1 stage-set-before-side-effect reorder (3 spots:
v1_dispatch_assessment, v1_voice_screening, v1_schedule_meeting); voice webhook CAS
hardening V-C1/2/3 + V-M5 unique constraint (only matters if voice volume is live).

2026-06-21 (cont) : PER-STAGE MODEL REGISTRY (replaces vague client.smart/fast).
Verified: `compileall src` CLEAN + 28 unit tests pass (5 registry + 23 engine).
- NEW: src/llm/model_registry.py -- single source of truth `STAGE_MODELS:
  dict[Stage, model_id]` + `model_for(stage)` resolver. Stage is a StrEnum
  (RESUME_FIT_SCORE, CLASSIFY_EMAIL, VOICE_SCREEN_EVAL, MEETING_ANALYSIS,
  PULSE_AGENT, ...). Cheap structured work -> gpt-4o-mini; heavier judgment
  (fit/screening/voice eval, meeting analysis, ranking, briefs, JD drafting,
  Pulse) -> gpt-4.1-mini. Ops `_OVERRIDES` hook + DEFAULT_MODEL fallback. Import
  stays light (cost-guard import is lazy) so it's unit-testable without litellm;
  cost guard still validates the table at boot when deps exist.
- WIRED every live LLM call site off client.smart / client.fast /
  _settings.llm_model_* to model_for(Stage.X): fit_score, parse_resume, rejection,
  offer, ceo_brief, screening gen+eval, voice gen+eval, meeting_analysis,
  journey_report, candidate_ranking, candidate_intent, voicemail, score_responses,
  interview_report, role drafting (draft/section/linkedin), assignment gen+eval+
  parse, answer_candidate_question, recruiter Pulse loop, classify_email. Only the
  retired supervisor still references llm_model_smart (intentionally left).
- NEW TEST: tests/unit/test_model_registry.py (every stage mapped, all cheap/
  allowed, resolver by enum+string, default fallback).
EMAIL CLASSIFIER FINDING (answered user): the LLM "is this a real application /
spam" gate (CLASSIFY_EMAIL_V1, "You are an email classifier...") lives in
activities/classify.py but that module is [SCRAPE] dead -- the LIVE intake path
(services/mail_ingest.py) does NOT use it. mail_ingest filters by a required
subject prefix + role match + resume-attachment detection, and only runs
classify_candidate_intent for replies from KNOWN candidates. So the authenticity
gate is currently bypassed; re-wiring it before parse/fit is a deliberate future
task (now would use model_for(Stage.CLASSIFY_EMAIL)).
FRONTEND TODO (noted, not built -- backend-only phase): show the role's company
persona/context in the JD-creation UI (data already on roles.company_context).

2026-06-21 (cont) : DOCUMENTED email-threading bug as NB-12 in architecture.md
(table row + full detail) and appended a consolidated "Progress Tracker" section
to architecture.md (DONE: Stage 1 scoring, Stage 2 engine, SP0 crashes+trust,
model registry; PENDING backend: NB-12 email threading [HIGH], revive email
authenticity gate [HIGH], P-1 stage-ordering, Graph webhook drops applicants,
voice CAS, retire pipeline_template, Langfuse resilience, CEO-brief guards;
PENDING frontend: deferred). architecture.md is now the single source of truth for
status. NB-12 summary: inbound replies are (a) routed to the dead supervisor bus
(silently dropped) and (b) matched only by sender->most-recent-app with no thread
headers (misroutes when a candidate has 2 roles; subject "application*" re-intakes
a reply). Fix scoped: capture In-Reply-To/References, thread-link to application,
route to domain_events, never re-intake a reply.

2026-06-22 : VOICE WEBHOOK IDEMPOTENCY GUARD (V-C1/V-C2/V-C3/V-M5). Approach: a
dedicated `processing_status` on voice_calls (separate from the call lifecycle
`status`), claimed exactly once via an atomic CAS, so the 3 ingest paths (live
webhook, /recover endpoint, watchdog) + ElevenLabs re-deliveries can never
double-run the evaluator. Verified: `compileall src` CLEAN + 28 unit tests pass.
FOUNDATION (lead): models/v1.ProcessingStatus enum (pending/processing/processed/
failed); voice_calls.processing_status column (+index, migration 0034); repo
helpers claim_processing (CAS pending|failed->processing, returns winner),
mark_processing_done, mark_processing_failed, sweep_stale_processing(>15min
processing->pending, for crashed workers); migration 0034 also backfills existing
transcripts to 'processed' and adds a partial UNIQUE(provider, provider_call_id)
WHERE provider_call_id IS NOT NULL (V-M5, dedupes existing dup rows first).
WIRING: webhooks_voice.elevenlabs_webhook -- replaced the brittle claim_completion
transcript-sentinel hack with claim_processing; mark_processing_done at every
terminal branch (confirmation, informational, callback, regular completion),
mark_processing_failed on early-disconnect. webhooks_voice.recover endpoint -- claim
before recovering (skip if already claimed), mark done/failed at its branches
(V-C2). webhook_watchdog._try_recover_from_elevenlabs -- claim before ingest, mark
done/failed, return False if not claimed (V-C3); run loop sweeps stale processing
each cycle before checking stuck calls (V-C1). Old claim_completion marked [SCRAPE]
(dead, superseded). Generalizable: same processing_status pattern can later guard
assignment-parse + meeting-analysis ("every AI step").
NOTE: graph webhook + classify_email gate left aside per user.

2026-06-22 : INBOUND-EMAIL FUNNEL -- NB-12 (threading) + authenticity gate.
Replaced the single `subject.startswith("application")` intake gate with a pure,
unit-tested funnel `services/email_filter.classify_inbound` -> reply | internal |
application | ignore (first match wins). DB-free/LLM-free like pipeline_engine;
16 unit tests (tests/unit/test_email_filter.py). Heuristic (100% deterministic per
user): reply = carries In-Reply-To/References (NEVER a new app); internal = sender
domain in org domains (referral if internal + resume + role subject); application =
external + (resume attachment + >=1 subject signal, OR strong subject alone =
keyword + role); else ignore. The dormant CLASSIFY_EMAIL_V1 LLM gate is left in
place as a future borderline tiebreaker.
THREADING (NB-12): imap_inbox parses In-Reply-To/References into InboundMessage
(+ has_reply_headers / referenced_message_ids). webhooks_email pulls
internetMessageId + internetMessageHeaders from Graph and adapts to the same
InboundMessage via _inbound_from_graph, reusing the SAME funnel + _route_reply.
Replies thread-match to the EXACT application via email_sends.provider_message_id
(our outbound Message-IDs), fallback = sender's most-recent active app; emitted as
a DURABLE domain_event candidate_email_reply (requires_action when withdrawal/
reschedule/question) -- replacing the dead supervisor bus that dropped + misrouted
replies. Replies are never re-intaken.
ORG DOMAINS: stored as organizations.settings["email_domains"] (JSON list, no new
table per user). repo: organization.get_email_domains/set_email_domains (clean,
lower-cased, @-stripped, subdomain-aware). Seed migration 0035 backfills the
default org's domains from configured from/careers addresses (default grabon.in),
idempotent (only when empty), preserves hand-edits.
ORG SCOPING FIX: create_application now stamps org_id from the default org when not
passed (was never set anywhere) -- so durable events (replies + the already-shipped
parked-gate events) are visible to the future org-scoped inbox (open_actionable).
Graph: NEW-applicant ingestion stays deferred to the IMAP poller (the reliable
floor that also fetches attachments); Graph only routes replies durably -> no
double-intake. processed_messages shared across both paths (keyed by Message-ID).
Verified: compileall src CLEAN; 44 dep-light unit tests pass (16 email + 23 engine
+ 5 registry).
FLAGGED (not yet done): "P-0 tail genericization" -- v1_meeting_analysis +
approval endpoints still hardcode the tech->ceo->hr->offer chain; a role configured
"tech then offer" (no CEO/HR) stalls after the tech round. The front half (intake->
fit->auto_progress) is already engine-generic; the back half needs the same
treatment (route post-interview transitions through auto_progress/pipeline_engine).

2026-06-22 : GENERIC STAGE-RUNNER (fix/10, kills P-0; delivers configurable any-N
pipeline). End state: every candidate carries per-stage outcome on the APPLICATION
(stage_results JSONB, migration 0036) -- processing_status (unprocessed|processing|
processed|failed) + verdict (pending|on_going|pass|fail) + result_ref. "Processed vs
not" is DERIVED from current_stage_key + the role template (position); the overlay
stores only what position can't (verdict + processing claim). Chose Option B (derive,
don't materialize) over a per-candidate stage table -- consistent with SP2 principle
#4 + 09-runtime-wiring; same achievement, less code, no JD-edit drift.
FOUNDATION: applications.stage_results column (base.py + migration 0036);
models/pipeline.StageVerdict enum + StageType.EMAIL_FILTER; pipeline_engine marks
email_filter + fit INLINE (engine walks past, never double-fires); repo helpers
(v1_application): claim_stage_processing (CAS idempotency), record_stage_verdict,
mark_stage_failed, candidate_stage_view (template JOIN overlay -> full per-candidate
stage list). DRIVER: services/stage_runner.advance_candidate(app, completed_stage_key,
verdict, result_ref) -- records verdict, then PASS->auto_progress (engine picks next),
FAIL->auto_reject (+rejection email), ON_GOING->hold. record_email_filter_passed +
record_fit_verdict make those inline stages trackable.
CUTOVER (the 6 decision points -- no evaluator hardcodes its successor anymore):
pipeline/v1 post-fit (email_filter+fit verdicts -> runner), post-screening (verdict ->
runner), assignment tail (verdict -> runner; tech-panel email demoted to notification,
no forced TECHNICAL_PENDING_APPROVAL); v1_meeting_analysis (deleted the
tech->ceo->hr->offer chain; MODE-AWARE: auto stage acts on verdict, manual stage holds
on_going + raises review_interview_analysis inbox event for human approve/reject);
v1_evaluate_voice_call (voice verdict -> runner); api/meetings approval gates
(_advance_from_gate: approve->PASS, reject->FAIL; guard now checks current_stage_key
with legacy fallback); v1_dashboard tech/ceo decision advance branches -> runner.
auto_progress unchanged (already the engine IO shell). create_application already
stamps org_id.
BACK-COMPAT: legacy current_stage kept as dual-written projection (40+ readers + ~82
error-park set_stage calls untouched). Legacy enum removal is a separate later pass.
VERIFIED: compileall src CLEAN; 52 dep-light unit tests pass (new test_generic_pipeline:
1-step [email_filter], no-CEO/HR tech->offer = the P-0 case, 2-interview reorder,
auto-decision skip, disabled-stage skip, verdict enum). DB-touching runner paths
covered by logic via the pure planner (sqlalchemy not installed locally).
NET: owner's 4 requirements met -- (#1) generic mode-agnostic processing, (#2) 1-step
JD works, (#3) configure->store->fetch->mark processed->next, (#4) per-candidate
per-stage status+verdict incl. on_going. P-0 fixed.

2026-06-22 09:00 UTC : P-1 stage-set-before-side-effect FIX + 3 LOW items closed

--- P-1: Dispatcher lifecycle fix (MED) ---

All 3 dispatchers now use claim_stage_processing -> do side-effect -> record_stage_verdict/mark_stage_failed:

- v1_dispatch_assessment.py: claim before send_assignment_email; on success verdict=on_going;

  on failure mark_stage_failed (visible + retryable). Skipped assignment records verdict=pass.

- v1_voice_screening.py: claim before set_stage; on successful provider.create_call verdict=on_going;

  on failure mark_stage_failed alongside existing mark_failed on voice_calls row.

- v1_schedule_meeting.py: claim before set_stage; after meeting booked + invites sent verdict=on_going.

Before: stage advanced (committed) BEFORE the external call; failure left a lying stage.

After: stage is claimed (processing), side-effect runs, then verdict recorded. Failures are

visible (processing_status=failed) and retryable.

FILES: src/activities/v1_dispatch_assessment.py, v1_voice_screening.py, v1_schedule_meeting.py



--- LOW: Retire pipeline_template JSONB ---

- recruiter_agent/tools.py create_role: now seeds role_pipeline_stages rows from the

  resolved template (maps step IDs to stage_type/stage_key/mode). Falls back to

  seed_default when no custom template provided.

- api/roles.py create endpoint: seeds role_pipeline_stages via seed_default after flush.

- services/pipeline_templates.py: marked DEPRECATED. Module retained for preset

  lookup and backward-compatible validation only.

FILES: src/recruiter_agent/tools.py, src/api/roles.py, src/services/pipeline_templates.py



--- LOW: Langfuse resilience (LF-H1/H2/M1..M5) ---

- LF-H1: prompt_manager.py rewritten with retry-with-backoff init (60s normal,

  300s after 3 consecutive failures). No more one-shot permanent failure.

- LF-M1: stale-while-revalidate -- on fetch failure, keeps last good fetched value

  instead of overwriting with hardcoded fallback.

- LF-H2: cache TTL reduced 60s -> 30s to narrow cross-worker mixed-version window.

- LF-M2: client.py callback registration uses set-based dedup (no duplicate traces).

- LF-M4: Langfuse compat shim now logs at debug instead of bare pass.

- LF-M5: invalidate_cache uses prefix match (clears ALL labels, not just 2 hardcoded).

FILES: src/llm/prompt_manager.py (full rewrite), src/llm/client.py (targeted edits)



--- LOW: CEO-brief hallucination guards (PR-4..7) ---

- v1_ceo_brief.py: generator passes "DATA_NOT_AVAILABLE" sentinel for empty/null data

  blobs (fit_score, screening_evaluation, voice_block, assessment_block, technical_block).

- ceo_brief.py: prompt v2 -- added guard instruction ("omit sections with unavailable

  data"), removed hardcoded GrabOn company culture, all sections conditional on data

  availability, Cultural Fit section uses JD-implied values instead of hardcoded traits.

FILES: src/llm/prompts/ceo_brief.py, src/activities/v1_ceo_brief.py



VERIFIED: compileall src CLEAN; 52 unit tests pass. All temp scripts deleted.

architecture.md Progress Tracker updated (P-1, Langfuse, CEO-brief, pipeline_template

all marked DONE).


---

## 2026-06-22 17:25 IST : Prompt generification + ArtifactPanel rewrite + Pydantic fix

### What
Comprehensive fix for prompt outdatedness, frontend artifact panel bugs, and LLM validation errors.

### Backend changes

1. **RoleDraftAssignment clamping** (`models/artifacts.py`):
   - Removed `ge=/le=` Pydantic constraints that caused `invalid_role_draft` errors (e.g. `deadline_days: -13`).
   - Added `@model_validator` that clamps values to valid ranges instead of rejecting.

2. **Pulse runner company name** (`recruiter_agent/runner.py`):
   - Replaced hardcoded `_COMPANY_NAME = "GrabOn"` with `_get_company_name()` that reads from `config.voice_agent_company_name`.

3. **Pulse system prompt anti-hallucination** (`recruiter_agent/prompts.py`):
   - Added hard anti-pattern: "NEVER output a JD as inline text in chat. ALL role drafts MUST go through `propose_role_draft`."

4. **Made ALL prompts generic** (removed GrabOn/InspireLabs hardcoding):
   - `classify_email.py` v2: `{company_name}` placeholder, generic language detection
   - `rejection_message.py` v2: `{company_name}` placeholder, generic tone
   - `interview_report.py` v2: uses `{company_context_json}` + `{evaluation_spec_json}` instead of hardcoded culture
   - `journey_report.py` v4: uses `{company_context_json}` + `{evaluation_spec_json}`, generic culture assessment
   - `role_drafting.py` v7: entire system prompt genericized with `{company_name}` placeholder,
     removed hardcoded GrabOn culture, JD sections use `## About {company_name}`, LinkedIn post uses `{company_name}`
   - `section_rewrite` v2: generic voice

5. **Fixed activity callers** to pass new template variables:
   - `classify.py`: passes `company_name` from config
   - `v1_journey_report.py`: passes `company_context_json` + `evaluation_spec_json` from role
   - `report.py`: passes `company_context_json` + `evaluation_spec_json` from snapshot
   - `rejection.py`: `_get_company_name_for_rejection()` helper
   - `v1_role_drafting.py`: formats system prompt with `company_name`, LinkedIn system with `company_name` + `apply_email`
   - `v1_dashboard.py`: both rejection callers pass `company_name` + use `candidate_name` (was `name`)

### Frontend changes

6. **ArtifactPanel full rewrite** (`components/recruiter-chat/ArtifactPanel.tsx`):
   - Collapsible sections with chevron toggle (Basics, JD, Pipeline, Evaluation, Company Context, Assignment, Notes)
   - **ALL fields now editable**: company_context (intensity, summary, what_matters_here, hiring_bar), knockouts, max_notice_days, notes
   - Pipeline stages have human-readable labels, proper checkbox styling with brand-green accent
   - Evaluation dimensions have labeled sections for what_good_looks_like and anti_signals with proper placeholder text
   - Assignment section has min/max constraints on inputs
   - Default width 520px (was 480), resize range 400-900px
   - Proper shadcn + tailwind styling throughout

### Prompts NOT changed (already generic)
- `fit_score.py` - already uses `{company_context_json}` + `{evaluation_spec_json}`
- `screening_gen.py` - already generic
- `screening_eval.py` - already generic
- `voice_screening.py` - already generic
- `assignment_parse.py` - already generic
- `meeting_analysis.py` - already generic
- `ceo_brief.py` - already fixed in prior session (v2)
- `score_open_text.py` - already generic
- `parse_resume.py` - already generic

### Validation
- `compileall src` CLEAN
- 52 unit tests pass
