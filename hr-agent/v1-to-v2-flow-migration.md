# V1 → V2 Stage Management Migration

## Why This Matters

There are **two parallel stage-management systems** writing to the same `current_stage_key` column on `applications`:

| System | Writer | Stage source |
|--------|--------|-------------|
| **V1 (legacy)** | `set_stage()` in `v1_application.py` | Hardcoded 35-entry `_LEGACY_STAGE_KEY` map |
| **V2 (current)** | `advance_candidate()` → `auto_progress()` → `plan_transition()` | Per-role `role_pipeline_stages` config |

They **race**. V1 writes `current_stage_key` through a lossy mapping (`report_ready → assessment_review`) that may not exist in the role's actual pipeline. V2 writes it based on the pipeline config. Whichever runs last wins — and they run in different sessions with no ordering guarantee.

### Bugs This Has Already Caused

1. **Assignment re-dispatched** (`pipeline/v1.py`):
   `set_stage(PipelineStage.REPORT_READY)` mapped to `current_stage_key = "assessment_review"`. That stage wasn't in the role's pipeline (`intake → fit_score → assignment → offer`). V2 `advance_candidate` then called `plan_transition` which couldn't find `assessment_review` → reset to `start = -1` → picked `assignment` as the first actionable stage → **re-sent the assignment email**.

2. **Lossy mapping ambiguity**: Multiple V1 stages map to the same V2 key with conflicting statuses:
   ```
   "report_ready"           → ("assessment_review", "active")
   "assessment_completed"   → ("assessment_review", "active")
   "assessment_evaluated"   → ("assessment_review", "completed")
   ```
   V1's `set_stage` overwrites the V2 `stage_status` with a stale value.

3. **Silent fallback bypass** (`auto_progress.py:97-100`):
   The legacy→V2 fallback only runs when `current_stage_key IS None`. If V1 already wrote a stale key, the fallback is silently skipped and the stale key drives progression.

---

## Architecture: What Stays vs What Goes

### DELETE — Pure V1 Stage Infrastructure (3 files)

These files define the V1 stage enum, transition rules, and the dual-write bridge. They have **zero business logic**.

| File | What it contains | Lines |
|------|-----------------|-------|
| `backend/src/models/v1.py` | `PipelineStage` enum (30 values), `ALLOWED_TRANSITIONS` DAG, `can_transition()` | 22–232 |
| `backend/src/db/repositories/v1_application.py` | `set_stage()`, `claim_stage_processing()`, `record_stage_verdict()`, `mark_stage_failed()`, `candidate_stage_view()` | 31–290 |
| `backend/src/services/state_machine.py` | `_LEGACY_STAGE_KEY` mapping dict, `legacy_stage_to_key()` bridge function | 33–74 |

**What the V1 repo does** vs **What V2 replaces it with**:

| V1 function | V2 equivalent |
|-------------|---------------|
| `set_stage(session, app_id, stage, force)` | `advance_candidate(application_id, completed_stage_key, verdict, result_ref)` from `stage_runner.py` |
| `record_stage_verdict(session, app_id, key, verdict, result_ref)` | Same function exists in V2 — but is called via `advance_candidate` |
| `mark_stage_failed(session, app_id, key, error)` | `advance_candidate(application_id, key, verdict=FAIL, result_ref={"error": ...})` |
| `claim_stage_processing(session, app_id, stage_key)` | V2 doesn't need this — stages are claimed via `stage_results.processing_status` |
| `candidate_stage_view(session, app_id)` | Already uses V2 `current_stage_key` + `stage_results` — just update the import |
| `legacy_stage_to_key(stage)` | Delete entirely — V2 uses `current_stage_key` directly |

### KEEP AS-IS — Pure V2 Stage Infrastructure (12 files)

These files define or use the V2 pipeline engine. They do not import or call V1 stage management.

| File | What it does | Why keep |
|------|-------------|----------|
| `backend/src/models/pipeline.py` | `StageType`, `StageMode`, `StageVerdict`, `StageStatus`, `DEFAULT_PIPELINE`, `PipelineStageConfig` | Core V2 data model |
| `backend/src/services/pipeline_engine.py` | `plan_transition()` — pure decision core, no IO | The planner that determines next stage |
| `backend/src/services/stage_runner.py` | `advance_candidate()` — verdict-aware progression driver | The single V2 entry point for stage advancement |
| `backend/src/db/repositories/role_pipeline_stage.py` | V2 per-role pipeline CRUD (`for_role`, `get_stage`, `next_stage`, `seed_default_pipeline`) | Reads/writes `role_pipeline_stages` table |
| `backend/src/db/base.py` | `RolePipelineStage` ORM model | Needed for V2 pipeline storage |
| `backend/src/services/evaluation.py` | Score→`StageVerdict` mapping with thresholding | Pure V2, no V1 deps |
| `backend/src/models/artifacts.py` | Artifact data shapes using `PipelineStageDef` | V2-native data models |
| `backend/src/api/recruiter_chat.py` | Seeds V2 pipeline directly via `RolePipelineStage` | Already V2-native |
| `backend/src/services/pipeline_templates.py` | Stage-to-template-step mapping | Uses V1 enum values as data keys — needs migration to V2 stage keys |
| `backend/src/api/ceo_dashboard.py` | CEO read-only view | Only uses V1 `PipelineStage` as filter constants |
| `backend/src/api/hr_dashboard.py` | HR read-only view | Only uses V1 `PipelineStage` as filter constants |
| `backend/src/services/auto_progress.py` | V2 progression engine | Core V2 logic — just needs V1 fallback calls swapped |

### REWIRE — Business Logic That Calls V1 `set_stage()` (24 files)

These files do **real work** (LLM calls, file uploads, email sending, calendar sync, etc.) and also call V1 `set_stage()`. The business logic stays — only the stage write calls change.

---

## The Rewire Pattern

Every `set_stage()` call site follows one of these replacement patterns:

### Pattern 1: Stage completed successfully (PASS)

```python
# OLD
await set_stage(session, app_id, PipelineStage.REPORT_READY, force=True)

# NEW
await advance_candidate(
    application_id=app_id,
    completed_stage_key="assignment",
    verdict=StageVerdict.PASS,
    result_ref={"stage": "assignment", "report": "ready"},
)
```

### Pattern 2: Stage failed / candidate rejected (FAIL)

```python
# OLD
await set_stage(session, app_id, PipelineStage.REJECTED, force=True)

# NEW
await advance_candidate(
    application_id=app_id,
    completed_stage_key=current_stage_key,
    verdict=StageVerdict.FAIL,
    result_ref={"reason": "..."},
)
```

### Pattern 3: Needs human review (NEEDS_HR_REVIEW → held/parked)

```python
# OLD
await set_stage(session, app_id, PipelineStage.NEEDS_HR_REVIEW, force=True)

# NEW
# Just record verdict — auto_progress will park at the next manual gate
await advance_candidate(
    application_id=app_id,
    completed_stage_key=current_stage_key,
    verdict=StageVerdict.ON_GOING,
    result_ref={"reason": "needs_human_review"},
)
```

### Pattern 4: Stage dispatched (assignment sent / voice scheduled)

```python
# OLD
await set_stage(session, app_id, PipelineStage.ASSIGNMENT_SENT, force=True)

# NEW
# The stage is already set when advance_candidate moved here.
# The dispatch is a side-effect of being AT this stage — no stage write needed.
await record_stage_verdict(
    session, app_id, "assignment",
    verdict="on_going",
    result_ref={"dispatched": True},
)
```

---

## Inventory of All Call Sites

### Group 1: Activities (`backend/src/activities/`)

#### `v1_dispatch_assessment.py` — 2 calls

| Line | Old call | Replacement |
|------|----------|-------------|
| 51 | `set_stage(..., ASSIGNMENT_SENT)` | `record_stage_verdict("assignment", "on_going", ...)` — dispatch is a side-effect |
| 55 | `set_stage(..., ASSESSMENT_EVALUATED)` | `advance_candidate("assignment", PASS, ...)` |

**Business logic kept**: Assignment email generation, file attachment, SMTP send.

#### `v1_dispatch_voice_call.py` — 1 call

| Line | Old call | Replacement |
|------|----------|-------------|
| 110 | `set_stage(..., VOICE_SCREEN_SCHEDULED)` | `record_stage_verdict("voice_screen", "on_going", ...)` |

**Business logic kept**: ElevenLabs API call, voice call record creation.

#### `v1_evaluate_voice_call.py` — 5 calls

| Line | Old call | Replacement |
|------|----------|-------------|
| 122 | `set_stage(..., NEEDS_HR_REVIEW)` | `advance_candidate("voice_screen", ON_GOING, ...)` |
| 292 | `set_stage(..., VOICE_SCREEN_EVALUATED)` | `advance_candidate("voice_screen", PASS, ...)` |
| 295 | `set_stage(..., NEEDS_HR_REVIEW)` | `advance_candidate("voice_screen", ON_GOING, ...)` |
| 337 | `set_stage(..., REJECTED)` | `advance_candidate("voice_screen", FAIL, ...)` |
| 445 | `set_stage(..., REJECTED)` | `advance_candidate("voice_screen", FAIL, ...)` |

**Business logic kept**: LLM transcript evaluation, Gemini audio eval, score computation.

#### `v1_schedule_meeting.py` — 2 calls

| Line | Old call | Replacement |
|------|----------|-------------|
| 230 | `set_stage(..., NEEDS_HR_REVIEW)` | `advance_candidate(stage_key, ON_GOING, ...)` |
| 291 | `set_stage(..., round_to_stage[round])` | `advance_candidate(stage_key, PASS, ...)` |

**Business logic kept**: Calendar sync, slot matching, panel coordination, invite sending.

#### `v1_voice_screening.py` — 1 call

| Line | Old call | Replacement |
|------|----------|-------------|
| 191 | `set_stage(..., VOICE_SCREEN_SCHEDULED)` | `record_stage_verdict("voice_screen", "on_going", ...)` |

**Business logic kept**: ConvAI orchestration, response processing, scoring.

#### `offer.py` — 1 call

| Line | Old call | Replacement |
|------|----------|-------------|
| 138 | `set_stage(..., HIRED)` | `advance_candidate("offer", PASS, ...)` — or just set dedicated `HIRED` status |

**Business logic kept**: LLM offer letter generation, document storage, email.

#### `v1_meeting_analysis.py` — **Already V2**

Line 272 already calls `advance_candidate()`. No change needed.

---

### Group 2: Pipeline Orchestrator (`backend/src/pipeline/v1.py`) — 4 calls

| Line | Old call | Replacement |
|------|----------|-------------|
| 99 | `set_stage(..., NEEDS_HR_REVIEW)` | `advance_candidate("intake", ON_GOING, ...)` |
| 178 | `set_stage(..., REJECTED)` | `advance_candidate("email_filter", FAIL, ...)` |
| 208 | `set_stage(..., NEEDS_HR_REVIEW)` | `advance_candidate("fit", ON_GOING, ...)` |
| 344 | `set_stage(..., NEEDS_HR_REVIEW)` | `advance_candidate(completed_key, ON_GOING, ...)` |

**Business logic kept**: Full intake→parse→fit orchestration, LLM calls, file processing, notifications.

---

### Group 3: API Endpoints (`backend/src/api/`)

#### `v1_dashboard.py` — 12+ calls

| Line | Old call | Replacement |
|------|----------|-------------|
| 848 | `set_stage(..., REJECTED)` | `advance_candidate(..., FAIL)` |
| 891 | `set_stage(..., target)` | `advance_candidate(..., PASS/FAIL based on target)` |
| 990 | `set_stage(..., REJECTED)` | `advance_candidate(..., FAIL)` |
| 1252 | `set_stage(..., target)` | `advance_candidate(..., PASS/FAIL based on target)` |
| 1353 | `set_stage(..., REJECTED)` | `advance_candidate(..., FAIL)` |
| 1474 | `set_stage(..., REJECTED)` | `advance_candidate(..., FAIL)` |
| 1618 | `set_stage(..., REJECTED)` | `advance_candidate(..., FAIL)` |
| 1657 | `set_stage(..., HIRED)` | `advance_candidate("offer", PASS)` |

**Business logic kept**: Pagination, filters, bulk operations, file uploads, async processing.

#### `api/apply.py` — 4 calls

| Line | Old call | Replacement |
|------|----------|-------------|
| 228 | `set_stage(..., SCREENING_SUBMITTED)` | `advance_candidate("screening", PASS)` |
| 321 | `set_stage(..., ASSESSMENT_COMPLETED)` | `advance_candidate("assignment", PASS)` |
| 324 | `set_stage(..., ASSESSMENT_PENDING_REVIEW)` | Skip — duplicate write |
| 328 | `set_stage(..., ASSIGNMENT_SUBMITTED)` | -> triggers `run_assignment_processing` -> `advance_candidate` |

**Business logic kept**: Token validation, file uploads, R2 storage, submission processing.

#### `api/meetings.py` — 3 calls

| Line | Old call | Replacement |
|------|----------|-------------|
| 134 | `set_stage(..., round_to_stage[round])` | `advance_candidate(stage_key, PASS)` |
| 174 | `set_stage(..., VOICE_SCREEN_EVALUATED)` | `advance_candidate("voice_screen", PASS)` |
| 502 | `set_stage(..., new_stage)` | `advance_candidate(..., PASS/FAIL)` |

**Business logic kept**: Calendar operations, panel matching, decision recording.

#### `api/webhooks_voice.py` — 7 calls

| Line | Old call | Replacement |
|------|----------|-------------|
| 629 | `set_stage(..., VOICE_SCREEN_COMPLETED)` | `advance_candidate("voice_screen", PASS)` |
| 811 | `set_stage(..., NEEDS_HR_REVIEW)` | `advance_candidate("voice_screen", ON_GOING)` |
| 912 | `set_stage(..., callback)` | `advance_candidate("voice_screen", ON_GOING)` |
| 1052 | `set_stage(..., IN_PROGRESS)` | `record_stage_verdict(..., "on_going")` |
| 1182 | `set_stage(..., NEEDS_HR_REVIEW)` | `advance_candidate("voice_screen", ON_GOING)` |
| 1304 | `set_stage(..., COMPLETED)` | `advance_candidate("voice_screen", PASS)` |
| 1475 | `set_stage(..., EVALUATED)` | `advance_candidate("voice_screen", PASS)` |

**Business logic kept**: ElevenLabs webhook processing, transcript handling, emotion analysis, score computation.

---

### Group 4: Workers (`backend/src/workers/jobs.py`) — 2 calls

| Line | Old call | Replacement |
|------|----------|-------------|
| 434 | `set_stage(..., NEEDS_HR_REVIEW)` | `advance_candidate(stage_key, ON_GOING)` |
| 507 | `set_stage(..., NEEDS_HR_REVIEW)` | `advance_candidate(stage_key, ON_GOING)` |

**Business logic kept**: Nudge timing checks, stall detection logic.

---

### Group 5: Agent Tools (`backend/src/recruiter_agent/tools.py`) — 1 call

| Line | Old call | Replacement |
|------|----------|-------------|
| 1272 | `set_stage(..., target)` | `advance_candidate(..., PASS/FAIL)` |

**Business logic kept**: Role CRUD, candidate search, agent orchestration.

---

### Group 6: Tool Registry (`backend/src/tools/registry.py`) — 2 calls

| Line | Old call | Replacement |
|------|----------|-------------|
| 178 | `set_stage(..., target_stage)` | `advance_candidate(..., PASS/FAIL)` |
| 335 | `set_stage(..., REJECTED)` | `advance_candidate(..., FAIL)` |

**Business logic kept**: Tool execution dispatch, permission checking.

---

### Group 7: Services (`backend/src/services/`) — ~20 calls across 6 files

These are the V1 fallback calls inside V2 services:

| File | Line(s) | Old call | Replacement |
|------|---------|----------|-------------|
| `auto_progress.py` | 266 | `set_stage(..., REJECTED)` | Internal — already in V2 flow |
| `auto_progress.py` | 302 | `set_stage(..., NEEDS_HR_REVIEW)` | Internal — already in V2 flow |
| `auto_progress.py` | 348 | `set_stage(..., NEEDS_HR_REVIEW)` | Internal — already in V2 flow |
| `auto_progress.py` | 399 | `set_stage(..., NEEDS_HR_REVIEW)` | Internal — already in V2 flow |
| `auto_progress.py` | 461 | `set_stage(..., NEEDS_HR_REVIEW)` | Internal — already in V2 flow |
| `auto_progress.py` | 98 | `legacy_stage_to_key()` fallback | Remove — V2 always has `current_stage_key` |
| `chat_meeting.py` | 308 | `set_stage(..., round_to_stage[round])` | `advance_candidate(...)` |
| `fallback_manager.py` | 39,118,150 | `set_stage(..., NEEDS_HR_REVIEW)` | `advance_candidate(..., ON_GOING)` |
| `panel_availability.py` | 420,691 | `set_stage(..., NEEDS_HR_REVIEW)` | `advance_candidate(..., ON_GOING)` |
| `smart_scheduler.py` | 126,164,286,762,791,950 | `set_stage(..., NEEDS_HR_REVIEW/scheduled_stage)` | `advance_candidate(...)` |
| `webhook_watchdog.py` | 133 | `set_stage(..., COMPLETED)` | `advance_candidate(..., PASS)` |

**Business logic kept**: All of it — scheduling, circuit breaking, watchdog, panel coordination.

---

## Migration Steps (Ordered)

### Step 1: Delete pure V1 infrastructure (3 files)
- Delete `backend/src/models/v1.py`
- Delete `backend/src/db/repositories/v1_application.py`
- Delete `backend/src/services/state_machine.py`
- Remove `current_stage` from ORM model (or deprecate)

### Step 2: Make `advance_candidate` the single entry point
- Ensure `advance_candidate()` in `stage_runner.py` handles all verdict paths (PASS, FAIL, ON_GOING)
- Ensure `auto_progress.py` doesn't fall back to `legacy_stage_to_key()`

### Step 3: Rewire all 60+ call sites (24 files)
Group by risk:
- **Low risk** (dispatch-only): `v1_dispatch_voice_call.py`, `v1_dispatch_assessment.py` — no side effects
- **Medium risk** (single failure path): `v1_voice_screening.py`, `offer.py`, `chat_meeting.py`, `webhook_watchdog.py`
- **High risk** (multiple paths): `v1_evaluate_voice_call.py`, `v1_schedule_meeting.py`, `v1_dashboard.py`, `webhooks_voice.py`
- **Highest risk** (orchestrator): `pipeline/v1.py`

### Step 4: Update V2 services that call V1 `set_stage()`
- `auto_progress.py`: Replace internal `set_stage()` calls with V2 equivalents
- `fallback_manager.py`, `panel_availability.py`, `smart_scheduler.py`: Pattern replace

### Step 5: Update guardrails and templates
- `supervisor/guardrails.py`: Replace `ALLOWED_TRANSITIONS` check with V2 pipeline lookup
- `services/pipeline_templates.py`: Migrate from V1 enum keys to V2 stage keys
- `api/ceo_dashboard.py`, `api/hr_dashboard.py`: Update filter constants from V1 enum values to V2 stage keys

### Step 6: Clean up DB
- Drop `applications.current_stage` column (after verifying no reads depend on it)
- Keep `applications.current_stage_key` as the single source of truth
- All progression reads `current_stage_key` directly

---

## Post-Migration State

After migration:

```
V1 PipelineStage enum        → DELETED
V1 ALLOWED_TRANSITIONS DAG   → DELETED  
V1 set_stage()               → DELETED
V1 _LEGACY_STAGE_KEY map     → DELETED
V1 legacy_stage_to_key()     → DELETED
applications.current_stage   → DELETED (or deprecated)

V2 plan_transition()         → Single decision engine
V2 advance_candidate()       → Single progression entry point
V2 role_pipeline_stages      → Per-role config = source of truth
V2 stage_results             → Verdicts per stage (JSONB)
V2 current_stage_key         → Single cursor field
```

**No more races. No more lossy mappings. No more stale key overwrites.**
