# SP2 — Data Foundation (the keystone)

> **STATUS (2026-06-22): BACKEND FOUNDATION DONE.** org schema + `org_id` keys, `role_pipeline_stages` (template), the single writer `set_stage()` (dual-writes `current_stage_key`/`stage_status`), durable `domain_events` backbone, and the expand migrations all shipped. The **progression flip** (make `role_pipeline_stages` the runtime source of truth) is the remaining piece -- specced in `10-generic-stage-runner.md`.

> The aggressive schema rebuild. Three things, one migration:
> **(1)** an org-scoped, normalized core; **(2)** one canonical state machine
> driven by **per-role pipeline templates**; **(3)** the durable **event → derived
> action inbox** backbone. Everything operational stands on this.
>
> Evaluation intelligence (persona + generated role specs) shares this migration
> but is documented separately in `03-evaluation-intelligence.md`.

---

## 1. Design principles

1. **Normalize what you query, join, and gate on** — identity, state, work,
   scheduling, seats. **Keep LLM outputs as versioned JSONB documents** — resume
   parse, eval blobs, reports, briefs, tool calls. Shredding LLM output into
   columns is the *opposite* mistake; it is genuinely document-shaped and
   prompt-versioned.
2. **One writer for stage.** `current_stage` is written by exactly one function,
   `transition()`, after side-effects succeed. Nothing else calls `set_stage`.
3. **The template is the source of truth** for what stages exist and how they
   behave. Stages are *instances* from a fixed type catalog, not a hardcoded enum.
4. **The inbox is derived, not maintained** — a query over the event log +
   application state + a small mutable overlay. No projection worker to drift.
5. **Tenant-ready, not multi-tenant.** `org_id` on every top-level entity, FK to
   `organizations`. One org row today. No RLS/billing yet.

---

## 2. New tables

### `organizations`
Tenant anchor. `id`, `name`, `slug`, `settings JSONB`, `created_at`.
One row (GrabOn) now. `org_id` FK added to: `candidates`, `roles`,
`applications`, `panel_members`, `users`, `domain_events`, `notifications`,
`config_settings`, `company_persona`.

### `users`
Real identity (wired in SP1, schema now).
`id`, `org_id`, `email UNIQUE`, `name`, `google_sub`, `avatar_url`,
`seat_status` (`active|invited|disabled`), `role` (`admin|member` — light, not
RBAC), `created_at`, `last_seen_at`.

### `domain_events` — the durable backbone
Append-only typed log. Sibling to `audit_log` but **typed for machine
consumption** and carrying the *actionable* flag.

| Column | Type | Notes |
|---|---|---|
| id | BIGSERIAL PK | sequential |
| org_id | UUID FK | |
| application_id | UUID FK (nullable) | role-level events have none |
| role_id | UUID FK (nullable) | |
| type | VARCHAR(64) INDEX | `applicant_intake`, `fit_scored`, `voice_evaluated`, `assignment_submitted`, `assessment_ready`, `meeting_analysis_ready`, `reschedule_requested`, `candidate_email_reply`, `voice_call_failed`, `decision_pending`, `stage_changed`, … |
| payload | JSONB | event-specific data (scores, slot, reason) |
| actor | VARCHAR(255) | `system`, `agent`, or user email |
| **requires_action** | BOOL INDEX | true ⇒ part of the inbox until resolved |
| **resolved_at** | TIMESTAMPTZ (nullable) | set when handled or app advances past |
| resolved_by | VARCHAR(255) | |
| created_at | TIMESTAMPTZ INDEX | |

Index: `(org_id, requires_action, resolved_at)` for the inbox query.

### `action_overlay` — the only mutable human state over derived items
`id`, `org_id`, `action_key VARCHAR(128)` (stable derived key, e.g.
`app:{id}:assessment_review` or `event:{id}`), `snoozed_until`, `assigned_to`
(FK users, nullable), `dismissed_at`, `last_seen_at`. UNIQUE `(org_id, action_key)`.

### `notifications` — the FYI feed (🔔)
`id`, `org_id`, `user_id` (nullable = org-wide), `type`, `title`, `body`,
`link`, `read_at`, `created_at`. Derived from `domain_events` that are
*informational* (`requires_action=false`), e.g. "bot joined", "email delivered".

### `company_persona`
See `03-evaluation-intelligence.md`. Org-scoped, static-but-editable seed for
role spec generation.

---

## 3. The state machine

### Stage type catalog (fixed)
```
intake · parse · fit · screening · voice_screen · assignment
· assessment_review · interview · decision · offer
· (terminal) hired · rejected · withdrawn
```
`interview` may appear multiple times (instances `tech_1`, `tech_2`, `ceo`, …).

### Per-role pipeline template (`roles.pipeline_template`, validated JSONB)
Ordered list. Each entry:
```jsonc
{
  "key": "tech_2",            // stable instance id
  "type": "interview",        // from the catalog
  "label": "System Design Round",
  "mode": "manual",           // auto | manual  (the automation line, per stage)
  "config": {                  // type-specific
    "panel_emails": ["..."],
    "duration_minutes": 60,
    "bot_enabled": true,
    "rubric_ref": "evaluation_spec.interview.tech_2"  // links to EVAL spec
  }
}
```
- **Default template** (seeded on existing roles in the backfill, so nothing
  regresses): `intake·auto → parse·auto → fit·auto → screening·auto →
  voice_screen·auto → assignment·auto → assessment_review·manual →
  interview"Technical"·manual → interview"CEO"·manual → interview"HR"·manual →
  decision·manual → offer`.
- **HR after assessment** (your requirement) falls out naturally — HR is just a
  late `interview` instance; the automation line is "everything before
  `assessment_review` is `auto`."
- Editing the list = your customization (remove HR, add `tech_2`, reorder).
  The JD dialog edits this; the runtime obeys it.

### `transition()` — the single writer
```
transition(application, to_stage_key, actor, reason):
  template = role.pipeline_template
  assert to_stage_key is a valid successor of application.current_stage_key
         in template (or a terminal: rejected/withdrawn)   # gating
  ... (side-effects already succeeded before this call) ...
  application.current_stage_key = to_stage_key
  application.current_stage_type = template[to_stage_key].type   # denormalized
  emit domain_event("stage_changed", payload={from, to, reason})
  publish Redis push (realtime SSE)
```
- Kills scattered `set_stage()`, advance-before-side-effect (`P-1`), and the
  "schedule a meeting for someone in the wrong stage" edge case (gating).
- **Auto vs manual:** the automated lane auto-advances `mode:auto` stages on
  their completion event; `mode:manual` stages stop and surface an action item.

### Retire the overloaded `NEEDS_HR_REVIEW`
Replace its three meanings (`NB-6`) with explicit derived action item *types*
(`assessment_review`, `meeting_scheduling`, `voice_call_failed`). The stage stays
simple; the action item carries the precise to-do. HR never again clicks the
wrong "push to assessment" button.

### `meeting_sessions` change
`round` literal → **`stage_key`** (template ref). Add partial
`UNIQUE(application_id, stage_key) WHERE bot_status IN ('pending','scheduled','in_call')`
(`NB-4`). Split overloaded `bot_id` into `calendar_event_id` + `bot_id` (`P-14`).

---

## 4. The derived inbox

The inbox is a **read model**, computed, not stored:

```sql
-- pseudo
SELECT * FROM (
  -- (a) stage-gated work: apps sitting at a manual stage
  SELECT 'app:'||a.id||':'||a.current_stage_key AS action_key,
         a.current_stage_type AS type, a.id AS application_id, ...
  FROM applications a
  WHERE a.org_id = :org
    AND a.status = 'active'
    AND template_mode(a) = 'manual'   -- current stage is human-gated
  UNION ALL
  -- (b) async exceptions: unresolved actionable events
  SELECT 'event:'||e.id, e.type, e.application_id, ...
  FROM domain_events e
  WHERE e.org_id = :org AND e.requires_action AND e.resolved_at IS NULL
) work
LEFT JOIN action_overlay o USING (action_key)   -- snooze/assign/dismiss
WHERE o.dismissed_at IS NULL
  AND (o.snoozed_until IS NULL OR o.snoozed_until < now())
ORDER BY priority DESC;
```
- **Priority** = f(type severity, SLA breach, fit tier, wait time) — computed in
  the service layer, cached per action_key (short TTL).
- **AI summary + pre-filled action** — computed on read and cached; the card
  shows "why this matters" + the primary action. (Detail in SP3.)
- **Resolution is implicit** for stage-gated work (the app advances → the row
  vanishes) and explicit for events (`resolved_at` set when handled).

A thin **reconciler** (replaces the dead supervisor and the auto_progress
sprawl) runs on each completion event: advances `auto` stages, sets
`requires_action`/`resolved_at` on the relevant events. It does *not* maintain an
action table — it just moves the state machine and flags events.

---

## 5. Normalization verdicts (per existing table)

| Table / field | Verdict |
|---|---|
| `applications.screening_evaluation`, `assignment_submission`, `journey_report` | **Keep JSONB** — LLM document output, read whole, prompt-versioned |
| `applications.current_stage` (+ new `current_stage_key`, `current_stage_type`) | **Normalize** — queried/gated constantly |
| `roles.pipeline_template`, `evaluation_spec`, `scoring_rubric` | **Keep JSONB, but validated schema** — config documents |
| `candidate_profiles.parsed_data`, `extraction_confidence` | **Keep JSONB** — LLM output |
| `evidence_records.candidate_id` | **Add FK** to `candidates` (`NB-10`) |
| `meeting_sessions.round` → `stage_key`; `bot_id` split | **Normalize** (above) |
| `voice_calls.provider_call_id` | **Add UNIQUE** (`V-M5`) |
| supervisor_* tables | **Drop** (retired) |
| chat-v2 tables (`conversations`,`messages`,`screening_answers`) | **Drop** — migration `0030` already pending |

---

## 6. Migration — expand-contract (safe "aggressive" rebuild)

1. **Expand** (additive, zero downtime): create `organizations`, `users`,
   `domain_events`, `action_overlay`, `notifications`, `company_persona`; add
   nullable `org_id`, `current_stage_key`, `current_stage_type`,
   `meeting_sessions.stage_key`, `roles.evaluation_spec`.
2. **Backfill / port script** (idempotent, re-runnable):
   - create the GrabOn `organizations` row; stamp `org_id` everywhere;
   - seed the **default `pipeline_template`** on every existing role;
   - map each application's `current_stage` → `current_stage_key` (+ type);
   - backfill `meeting_sessions.round` → `stage_key`;
   - generate a `company_persona` draft from existing role/company context for
     review (EVAL doc);
   - open `requires_action` events for any in-flight human-gate states so nothing
     is lost at cutover.
3. **Cutover** (one short freeze): pause workers, run backfill, deploy the build
   that uses `transition()` + the reconciler + the derived inbox, resume.
4. **Contract** (follow-up migration): `org_id` NOT NULL; drop supervisor and
   chat-v2 tables; delete the 3 disabled scheduler paths and `auto_progress`
   sprawl now folded into the reconciler.

---

## 7. Implementation plan

1. Alembic migration for the Expand step (one revision).
2. `db/models`: ORM for the new tables; add columns; `pipeline_template` and
   `evaluation_spec` Pydantic schemas with validation.
3. `services/state_machine.py`: the `transition()` function + template successor
   logic + gating. Unit tests for valid/invalid transitions and multi-interview
   templates.
4. `services/reconciler.py`: replaces supervisor + `auto_progress`. On each
   completion event, advance `auto` stages or flag `requires_action`.
5. `services/inbox.py`: the derived-inbox query + priority + cache. (Surface in SP3.)
6. `db/events.py`: `emit_event()` writing `domain_events` + Redis push;
   `resolve_event()`.
7. Port script under `scripts/port_to_v2.py`, idempotent, dry-run flag.
8. Replace every `set_stage()` / `auto_progress` caller with `transition()`.
9. Backfill on staging, verify counts, then the freeze cutover.

## Acceptance criteria

- Exactly one code path writes `current_stage` (grep proves it).
- A role with HR removed and a 2nd technical round added runs end-to-end.
- The inbox query returns the right human-gate items with zero stored action rows.
- An event-sourced reschedule survives a server restart (no lost alert — `NB-7`).
- Backfill is idempotent; staging parity verified before cutover.
