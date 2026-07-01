# Stale / Dead Code Map — V1→V2 migration

> Goal: mark superseded V1 code `# [STALE]` (or `// [STALE]` in TS) so it stops being maintained, then remove it in a dedicated pass. Verified by reading files + grepping for live callers (2026-06-25).
>
> **Two tiers.** **A — CONFIRMED-DEAD**: no live caller; safe to comment `# [STALE]` now and delete later. **B — SUPERSEDED-BUT-REACHABLE**: a V1 path still wired to a live endpoint/job — needs a product decision before touching. **C — already-gone leads** (docs point at code that no longer exists; no action).
>
> Rule while marking: comment, don't delete, in this pass. A stale block stays compilable so nothing breaks; deletion + migrations come after a green test run.

---

## A. CONFIRMED-DEAD — comment `# [STALE]` now

### A1. Chat-V2 / agent generators (no live caller)
| path : symbol | why stale | evidence |
|---|---|---|
| `backend/src/agent/generators.py` : `gen_tailored_questions`, `extract_turn` | Chat-V2 generators the live LangGraph agent never calls | already `# [SCRAPE]`; no caller in `src` |
| `backend/src/agent/prompts/tailored_qs.py` (whole) | prompt for dead `gen_tailored_questions` | only importer is dead `generators.py` + `__init__` re-export |
| `backend/src/agent/prompts/extract.py` (whole) | prompt for dead `extract_turn` | same |
| `backend/src/agent/prompts/__init__.py` : `extract` / `tailored_qs` import + `__all__` rows | re-exports of the above | **KEEP** the `assignment` import (`ASSIGNMENT_GEN_V1` is live) |
| `backend/src/agent/schemas.py` : `TailoredQuestion`, `TailoredQuestionsOut`, `ExtractedTurn` | schemas for dead generators | already `# [SCRAPE]`; **KEEP `AssignmentBriefOut` — live** |

### A2. Temporal-era activities (`activities/__init__.py` registers nothing; no live caller)
| path : symbol | why stale | evidence |
|---|---|---|
| `backend/src/activities/score_responses.py` (whole) | text-screening scorer, Temporal-era | `# [SCRAPE]`; only test imports it. **KEEP `ScoreResult`** (lives in `models/screening.py`) |
| `backend/src/activities/report.py` : `run_interview_report`, `interview_report_activity`, `InterviewReportInput/Result` | superseded by `v1_meeting_analysis` + `v1_journey_report` | `# [SCRAPE]`; no live importer. **Also has a latent bug** — its `snapshot` dict never sets `company_context`/`evaluation_spec`, so the prompt always gets `{}` (irrelevant while dead) |
| `backend/src/activities/classify.py` : `run_classify`, `classify_activity`, `ClassifyInput/Output` | intake classifies inline; live classifiers are elsewhere | `# [SCRAPE]`; no importer |
| `backend/src/activities/schedule.py` : `run_book_interview`, `book_interview_activity`, `cancel_interview_activity`, `BookInterviewInput/Result`, `CancelInterviewInput` | Temporal-era booking/cancel | no caller. **KEEP `run_propose_slots`** (live: `api/dashboard.py:807`, `recruiter_agent/tools.py:1516`) |
| `backend/src/services/calendar_sync.py` : `create_event`, `cancel_event` | only used by dead `schedule.py` fns | `# [SCRAPE]`. **KEEP `find_free_slots`** (live via `run_propose_slots`) |

### A3. Prompts / enums / model-routing reachable only via dead A2
| path : symbol | why stale | evidence |
|---|---|---|
| `backend/src/llm/prompts/score_open_text.py` (whole) | only consumed by dead `score_responses.py` | only importer is dead + `__init__` re-export |
| `backend/src/llm/prompts/interview_report.py` (whole) | only consumed by dead `report.py` | only importer is dead + `__init__` re-export |
| `backend/src/llm/prompts/__init__.py` : `score_open_text` + `interview_report` rows | re-exports of above | transitively dead |
| `backend/src/models/llm_outputs.py` : `OpenTextScore`, `InterviewReport`, `ClassificationResult` (+ `models/__init__.py` re-exports) | outputs for dead scorers | `OpenTextScore`→score_responses, `InterviewReport`→report, `ClassificationResult`→classify (all dead). **KEEP `FitAssessment`, `CriterionScore`, `RejectionDraft`** |
| `backend/src/llm/model_registry.py` : `Stage.SCORE_OPEN_TEXT`, `Stage.INTERVIEW_REPORT` + their `_MODEL_FOR` rows | model routing for dead stages | only referenced by dead activities |
| `backend/src/api/settings.py` : catalog rows `extract_turn`, `tailored_qs`, `interview_report` | prompt-tuning UI rows for dead prompts | stale UI entries (**KEEP `classify_email` — live**) |

### A4. Legacy decision/booking endpoints with no FE caller
| path : symbol | why stale | evidence |
|---|---|---|
| `backend/src/api/v1_dashboard.py` : `tech_decision` (`:1421`), `ceo_decision` (`:1590`), `hr_decision` (`:1732`) | superseded by the V2 approval surface | FE has **no** `/tech-decision`/`/ceo-decision`/`/hr-decision` caller; `admin-review-panel.tsx` uses `/resolve-review` + `/agentic/*` (→ `_advance_from_gate` → `stage_runner`). Their reject branches also bypass the V2 runner (would be a bug if reachable). **Confirm no other client, then stale.** |
| `backend/src/api/meetings.py` : `_kick_schedule_meeting` (`:547`) | the approval gates now park via `_advance_from_gate`; this legacy booker is unused | zero callers in `src` |

### A5. Frontend orphans (no importer in `frontend/src`)
| path : symbol | why stale | evidence |
|---|---|---|
| `frontend/src/components/pipeline-stepper.tsx` (whole) | legacy hardcoded 8-step ladder (applied→fit→voice→assessment→technical→ceo→hr→outcome) | **no importer**; live one is `components/candidate-detail/pipeline-stepper.tsx` (V2, `stage_view`-driven). A trap if re-imported |
| `frontend/src/components/pipeline-stage.tsx` : `PipelineStage` (whole) | legacy stage-status renderer | **no importer** |

---

## B. SUPERSEDED-BUT-REACHABLE — decide before touching

| path : symbol | status | evidence / decision needed |
|---|---|---|
| `backend/src/activities/screening.py` : `run_send_screening` | text-screening, but reachable via live endpoints | `api/dashboard.py` `POST /dashboard/actions/send-screening/{id}` (`:782`), `/reengage/{id}` (`:841`), HR post-shortlist auto-send (`:733`). Retire those endpoints first |
| `backend/src/activities/v1_schedule_meeting.py` : `schedule_meeting` | legacy auto-booker, wired live | `POST /agentic/meeting/ai-schedule` (`meetings.py:370`) → Arq `workers/jobs.py:131`; FE `meetings.aiSchedule()`. Decide if AI-schedule stays |
| `backend/src/services/panel_availability.py` (`initiate_panel_availability`, `record_panel_slots`, `record_candidate_selection`) | legacy panel-negotiation, reachable | `api/panel_availability.py` endpoints + Arq `workers/jobs.py:195` + `v1_dashboard.py:1383` manual trigger. Decide if HR still uses it |
| `backend/src/services/smart_scheduler.py` : `initiate_smart_schedule` | candidate-dead | only the orphaned `workers/jobs.py:188` `smart_schedule_meeting` job references it; **no enqueuer found**. `handle_candidate_response` / `book_confirmed_meeting` ARE live — keep those |
| `backend/src/api/dashboard.py` : `_signal_workflow` | Temporal stub, no-op (no server) | called from 3 live dashboard endpoints; effectively a no-op. Gut vs keep |
| `backend/src/services/state_machine.py` : `transition()`, `advance()` | written for V2 but **nothing calls them** | the live driver is `stage_runner.advance_candidate` + `pipeline_engine`. **KEEP `legacy_stage_to_key`** (live in 5 modules). These two fns are intended-future API, not legacy — flag, don't delete |
| `backend/src/services/typed_event_bus.py` (whole) : `publish_event`, `claim_pending_events`, `mark_processed` | retired (no-ops when `enable_supervisor=False`, the default) | **still imported** by `api/admin_config.py`, `webhooks_*`, `stall_detector.py`, `interview_intelligence.py`, `contradiction_detector.py`, `reengagement.py`, `v1_application.py`. Cut/guard those call sites BEFORE deleting the file |
| `backend/src/supervisor/*` (`engine`, `perception`, `guardrails`, `autonomy`) + `SupervisorEvent`/`SupervisorAction` models + `supervisor_*` tables | retired; replaced by `db/events.py` `domain_events` | already `[SCRAPE/RETIRED]`; lifespan task in `api/main.py` is commented out. Tables need a drop-migration (not just a comment) |
| `frontend/src/lib/api/supervisor.ts` + `frontend/src/app/(app)/supervisor/page.tsx` | FE for the retired supervisor | dead once the supervisor API is removed; confirm the nav entry is gone |

---

## C. Stale leads (docs reference code already removed — no action)

- `auto_progress.py:503-548` "commented legacy auto-scheduler block" — **does not exist** (that range is live `_fire_confirmation_call`/`_fire_joining_details_call`).
- `auto_progress._fire_chat_screen` importing `run_apply_to_chat` — `run_apply_to_chat`/`_fire_chat_screen`/`chat_screen` return **zero matches**; already removed.
- Legacy `tech_decision`/`ceo_decision`/`hr_decision` *FE* endpoints — the FE never had them; the backend ones are the stale artifact (see A4).

---

## Recommended order
1. **Zero-risk now:** A1, A2, A3, A4, A5 — comment `# [STALE]` / `// [STALE]`; pure no-callers.
2. **Cut callers first, then stale:** B `typed_event_bus` (many live no-op imports), supervisor tables (drop-migration).
3. **Product decision (B):** text-screening endpoints, `ai-schedule`, panel-availability, `_signal_workflow`. `state_machine.transition/advance` = intended-future, keep.
4. After everything reads V2, do the deletion pass + drop-migrations as a separate, test-gated change.
