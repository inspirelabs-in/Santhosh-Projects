# BUILD INDEX — Backend weekend (2 devs, 60-40 split)

> How two people build the backend in parallel without colliding.
> Auth stays as-is (existing key-match) this round — see `06-sp1-auth-seats.md`.
>
> **The rule:** Phase 0 (schemas + types) is done **first and together**. It
> freezes the *contract* between the two modules. After that, Dev A and Dev B
> touch **disjoint files** and only meet at the frozen interface.

---

## The seam (why this split is clean)

```
        ┌──────────────── PHASE 0 (shared, blocking) ────────────────┐
        │  tables · ORM · types · EVENT TAXONOMY · function signatures │
        │  db/events.py (emit/resolve infra)  ·  migration (expand)    │
        └───────────────┬───────────────────────────┬─────────────────┘
                        │ freeze contract            │
        ┌───────────────▼──────────┐   ┌─────────────▼────────────────┐
        │  MODULE A  (~60%)         │   │  MODULE B  (~40%)             │
        │  Pipeline + Evaluation    │   │  Work Surface + Data APIs     │
        │  "the brain"              │   │  "the I/O"                    │
        │                           │   │                               │
        │  PRODUCES domain_events   │──▶│  READS domain_events          │
        │  OWNS transition()        │◀──│  CALLS transition()/resolve() │
        └───────────────────────────┘   └───────────────────────────────┘
```

A **produces** events and owns state changes. B **consumes** events and renders/
acts. They meet only at: the **event taxonomy**, the **`transition()` /
`emit_event()` / `resolve_event()` signatures**, and the **sub-resource response
schemas** — all fixed in Phase 0. That's the whole interface.

---

## PHASE 0 — Shared foundation (do this FIRST, pair on it)

Nothing else starts until this is merged to a shared `feat/foundation` branch.
Target: half a day, both devs.

**0.1 Migration (expand step)** — one Alembic revision, additive only:
- new tables: `organizations`, `users`, `domain_events`, `action_overlay`,
  `notifications`, `company_persona`;
- new nullable columns: `*.org_id`, `applications.current_stage_key` +
  `current_stage_type`, `meeting_sessions.stage_key`, `roles.evaluation_spec`;
- `evidence_records.candidate_id` FK; `voice_calls.provider_call_id` UNIQUE;
  `meeting_sessions` partial UNIQUE `(application_id, stage_key)`.

**0.2 ORM models** for the new tables (`db/base.py` / `db/models`).

**0.3 Types — the frozen contract** (`models/` Pydantic):
- `PipelineStageType` (catalog enum: intake/parse/fit/screening/voice_screen/
  assignment/assessment_review/interview/decision/offer/hired/rejected/withdrawn);
- `PipelineTemplate` (validated: ordered stage entries, `mode`, `config`);
- `EvaluationSpec` (validated: dimensions, weights sum to 100, knockouts);
- `CompanyPersona`;
- **`EventType` enum + per-type payload schemas** (the most important contract);
- response schemas for candidate sub-resources (`/trace`, `/voice`, …).

**0.4 Event infrastructure** (`db/events.py`) — shared, owned by neither module:
- `emit_event(type, *, org_id, application_id?, payload, actor, requires_action=False)`
  → writes `domain_events` + Redis push;
- `resolve_event(event_id, by)` → sets `resolved_at`/`resolved_by`;
- `transition()` **signature stub** (A implements the body next).

**0.5 Port script skeleton** (`scripts/port_to_v2.py`) — idempotent, dry-run flag.
Fill in during integration; stub the steps now (`02-sp2-data-foundation.md §6`).

> **Definition of done for Phase 0:** migration applies on a fresh dev DB; all
> types import; `emit_event`/`resolve_event` work; `transition()` signature is
> agreed and stubbed. **Tag the commit; freeze the event taxonomy + signatures.**

---

## MODULE A (~60%) — Pipeline + Evaluation Engine  ·  "the brain"

Owner builds the logic that *runs* candidates and *judges* them. Reads:
`01-sp0-stabilize.md`, `02-sp2-data-foundation.md §3`, `03-evaluation-intelligence.md`.

**Owns these files (B never touches):**
- `services/state_machine.py` — `transition()` body, template successor + gating
- `services/reconciler.py` — replaces supervisor + `auto_progress`; auto-advances
  `mode:auto` stages, flags `requires_action` on `mode:manual` gates
- `services/persona.py` — `company_persona` CRUD + seed
- `llm/prompts/role_eval_spec_gen.py` — generate `evaluation_spec` from persona+role
- `llm/prompts/*` scoring refactors — fit/screening/voice/assignment/meeting become
  spec-driven + deep per-dimension reasoning (bump versions)
- `activities/*` scoring callers — pass `role.evaluation_spec`
- the SP0 **auto-lane trust** fixes (rejection email `P-2`, `HIRED` `P-3`, stage
  ordering `P-1`) — they're pipeline behavior, they live here

**Task checklist (A):**
- [ ] SP0 crashes (`CRASH-1..4`, `V-M1`, `V-M3`, `PR-2`) — unblock the lane
- [ ] `transition()` + gating + tests (valid/invalid/multi-interview templates)
- [ ] reconciler: auto-advance vs flag; replace all `set_stage`/`auto_progress` calls
- [ ] pipeline-template runtime wiring (JD dialog config → runtime; default template)
- [ ] `company_persona` table CRUD + seed draft
- [ ] `ROLE_EVAL_SPEC_GEN` + JD-dialog save (the dead config comes alive)
- [ ] refactor 5 scoring prompts to spec-driven + cite-or-abstain reasoning
- [ ] auto-lane trust: rejection emails, `HIRED`, side-effect-then-stage

**A emits these events** (B depends on the names/payloads — frozen in 0.3):
`applicant_intake, fit_scored, voice_evaluated, assignment_submitted,
assessment_ready, meeting_analysis_ready, decision_pending, stage_changed,
voice_call_failed`.

---

## MODULE B (~40%) — Work Surface + Data APIs  ·  "the I/O"

Owner builds everything that *reads* the engine's output and lets a human act.
Reads: `04-sp3-operational-surface.md`, `05-sp4-candidate-workspace.md`.

**Owns these files (A never touches):**
- `services/inbox.py` — the derived-inbox query + priority + cache (`§02-SP2 §4`)
- `api/inbox.py` — `GET /inbox`, `POST /inbox/{key}/{snooze|dismiss|assign}`
- `api/actions.py` — direct action endpoints (approve-reschedule, mark-handled,
  retry-call) → call A's `transition()` / `resolve_event()`
- `api/candidates.py` — split the monolith into sub-resources: `/trace`,
  `/profile`, `/screening`, `/voice`, `/assignment`, `/interviews`, `/emails`,
  `/audit` (surface `extracted_text_preview` `NB-8`, recordings, eval reasoning)
- `api/conversations.py` — `/conversations`, `/conversations/[id]`
- thin frontend slice: render real data in the workspace + the `hydrate()` system-
  message fix (`NB-7`) — "show the raw data", not polish

**Task checklist (B):**
- [ ] `inbox.py` query against `domain_events` + application state + overlay
- [ ] inbox endpoints + overlay (snooze/dismiss/assign)
- [ ] direct action endpoints (call into A's frozen signatures)
- [ ] candidate sub-resource endpoints (split `v1_dashboard` monolith)
- [ ] surface buried data: assignment preview, recordings, eval reasoning, emails
- [ ] `/conversations/[id]` + `NB-7` hydrate fix
- [ ] retire dummy pages → filters (kill `/ceo/[id]`,`/hr/[id]` dupe `NB-11`)

**B consumes** A's events (read-only) and calls `transition()`/`resolve_event()`.
B never writes `current_stage` directly.

---

## Coordination rules

1. **Phase 0 merges first.** No module work on `main`/`development` until the
   foundation branch is in and the contract is tagged.
2. **Disjoint files.** The ownership lists above don't overlap. If you need a file
   the other owns, it's a contract change → talk, don't fork.
3. **Contract changes are a stop-the-world.** Changing an event payload or a
   function signature means a 5-minute sync, not a silent edit.
4. **B can build against stubs.** A delivers a working `transition()` early
   (even minimal) so B isn't blocked; B can seed fake `domain_events` rows to
   develop the inbox before A's reconciler is done.
5. **Integration last.** Merge order: Phase 0 → A engine → B APIs → one
   end-to-end test: *A emits `assessment_ready` → B's `/inbox` shows it → human
   acts via B's action endpoint → A's `transition()` advances → event resolved →
   item leaves the inbox.*

## Weekend definition of done

- A candidate flows automatically intake→assignment, then **parks as an inbox
  action** at assessment review, gets advanced by a human action, and the inbox
  updates — all on the new schema, surviving a restart.
- A coupon-editor role generates a sensible `evaluation_spec` (no "builder
  mindset") and scores a candidate with cited per-dimension reasoning.
- The candidate workspace shows every trace (incl. previously buried artifacts)
  via lazy sub-resources.

## Doc map

| Read this | For |
|---|---|
| `README.md` | the whole picture + decisions |
| `01-sp0-stabilize.md` | A's crash + trust fixes |
| `02-sp2-data-foundation.md` | Phase 0 + A's state machine + B's inbox query |
| `03-evaluation-intelligence.md` | A's persona + specs + scoring |
| `04-sp3-operational-surface.md` | B's inbox + Pulse + conversations |
| `05-sp4-candidate-workspace.md` | B's trace workspace + sub-resources |
| `06-sp1-auth-seats.md` | deferred — auth, after the weekend |
| `07-hardening-polish.md` | later — scheduling, Pulse bugs, design system |
| `08-pulse-agent.md` | Pulse context/memory fix (token budget + summary + recall tool) — drop-in code, pick up with SP5/SP6 |
