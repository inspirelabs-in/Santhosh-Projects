# HR Agent — Revamp Plan (`/fix`)

> **STATUS (2026-06-22): most of the backend spine is SHIPPED.** SP0 (crashes +
> auto-lane trust), SP2 foundation (org schema, `role_pipeline_stages`, single
> stage writer, durable `domain_events`), EVAL (persona + per-role generated
> evaluation specs + rubric-driven scoring), and 09 Stage 1 (dual-write, events,
> local-prompt scoring, `[SCRAPE]` marks) are done. Also done this round: voice
> webhook idempotency (V-C1/2/3, V-M5), inbound-email funnel + threading (NB-12),
> approval-gate backdoor killed (NB-1/2). **The one remaining backbone piece is
> the generic stage-runner (09 Stage 2): make `role_pipeline_stages` the runtime
> source of truth so ANY configured pipeline runs generically. Full spec:
> [`10-generic-stage-runner.md`](10-generic-stage-runner.md).** Frontend (SP3/SP4)
> + auth (SP1) remain deferred per owner. See per-doc STATUS banners + the
> `architecture.md` Progress Tracker for the granular list.

> Architecture + implementation plan for turning the current vibecoded HR Agent
> into a coherent, experience-first hiring platform.
> Authored from the forensic audit in `architecture.md`, the schema in `data.md`,
> `meeting-scheduling.md`, and `prompts.md`. Read those for the bug-level detail;
> this set is the plan to fix the system, not just the bugs.

---

## 1. The core diagnosis

The system computes a lot of intelligence — fit scores, voice evals, meeting
analyses, HR inputs, reschedule requests, new-applicant signals — and then
**buries every bit of it inside `/candidate/[id]`**. There is no operational
surface that answers *"what needs me right now."* The component meant to be that
brain — the supervisor engine — has **never executed once in production**
(`CRASH-3`: `complete()` missing `prompt_version`, fails on every call).

Almost every complaint is a symptom of this one gap:

| Symptom | Root |
|---|---|
| "No way to see today's updates" | No operational surface; intelligence dies in the profile |
| "Alerts only inside the profile" | Events are ephemeral Redis pub/sub, lost on restart (`NB-7`) |
| "Stages are shit / scattered" | 30-value enum, scattered `set_stage()`, overloaded `NEEDS_HR_REVIEW` (`NB-6`) |
| "Scoring criteria are wrong for the role" | Hardcoded dimensions/weights (e.g. "builder mindset" for a coupon editor) |
| "Monolithic 60K-char candidate page" | One endpoint joins 6+ tables, no lazy loading (`NB-9`) |
| "Other pages are dummy / redundant" | `/ceo` and `/hr` share an endpoint (`NB-11`); meetings/assessments are thin lists |
| "Key-based login, no real users" | No identity layer at all |

## 2. The reframe

Make **work itself a first-class thing**, and make **evaluation criteria
role-specific and generated**, not hardcoded.

- **A durable work backbone.** A typed `domain_events` log (Postgres, not just
  Redis) feeds a *derived* action inbox. Redis stays only as the realtime push.
  This kills the entire "alerts lost / buried" bug class.
- **Two lanes.** Everything up to assessment is **automated** (auto-advance,
  auto-reject *with email*). From assessment review onward is
  **human-in-the-loop** — and that human-loop work is exactly what the inbox
  holds. (Decision: confirmed with product owner.)
- **Per-role pipeline templates.** Stages are not hardcoded. A role's
  `pipeline_template` (ordered list from a type catalog) decides which stages
  exist, in what order, which are auto vs manual, and how each round is
  configured. Add a 2nd technical round, remove HR — by editing the list.
- **Persona-driven evaluation.** A DB-stored, editable **company persona** seeds
  an AI-generated **role evaluation spec** at JD time. Every scoring prompt reads
  that spec instead of hardcoded weights. See `03-evaluation-intelligence.md`.
- **Experience-first surfaces.** Home = Split **Inbox + Pulse**. The candidate
  page becomes a **hybrid trace + jump-rail** workspace that keeps every trace
  but is organized and lazy-loaded. The dummy pages collapse into filters.

## 3. Decisions locked (this planning session)

| Axis | Decision |
|---|---|
| Tenancy | Single-org now (GrabOn), **tenant-ready schema** (`org_id` keys, org-scoped config/persona). Multi-tenant infra later. |
| Home screen | **Split: Inbox (left) + Pulse (right)** |
| Rebuild posture | **Aggressive schema rebuild**, executed safely via expand-contract migration |
| Automation line | **Auto up to assessment; human-in-the-loop from assessment review onward** (configurable per stage via template `mode`) |
| Inbox model | **Derived** from event log + application state + a tiny mutable overlay — *not* a heavy projection worker (lighter, can't drift) |
| Candidate workspace | **Hybrid**: trace stream + sticky section jump-rail |
| Pipeline flexibility | **Ordered list from a type catalog** (add/remove/reorder, multiple interview rounds, per-stage auto/manual + config). No branching engine. |
| Evaluation | **Company persona (static, editable) → AI-generated role eval spec → rubric-driven scoring + deep reasoning** |
| Near-term build focus | **Backend** + minimal frontend to **surface the raw data**. Auth/polish last. |

## 4. The spine (sub-projects, dependency-ordered)

| # | Sub-project | Doc | Status priority |
|---|---|---|---|
| SP0 | **Stabilize** — crashes + auto-lane trust (rejection emails, stage-after-side-effect) | `01-sp0-stabilize.md` | **1st** |
| SP2 | **Data foundation** — org schema, one state machine, pipeline templates, action/event backbone, migration | `02-sp2-data-foundation.md` | **2nd** (keystone) |
| EVAL | **Evaluation intelligence** — persona, generated role specs, rubric-driven scoring, deep reasoning | `03-evaluation-intelligence.md` | **2nd** (with SP2) |
| SP3 | **Operational surface** — Split Inbox + Pulse, `/conversations/[id]` | `04-sp3-operational-surface.md` | **3rd** |
| SP4 | **Candidate workspace** — trace+rail, lazy sub-resources, surface buried data | `05-sp4-candidate-workspace.md` | **3rd** |
| SP1 | **Identity & seats** — Firebase Google login, `users`/`org`, middleware | `06-sp1-auth-seats.md` | **4th** |
| SP5–7 | **Hardening & polish** — scheduling, Pulse state-machine bugs, design system | `07-hardening-polish.md` | **last** |
| — | **Pulse agent** — context/memory (token budget + rolling summary + recall tool), loop-vs-LangGraph, stale-doc cleanup | `08-pulse-agent.md` | with SP5/SP6 |

## 5. How to execute

**Coding this weekend? Start at [`00-BUILD-INDEX.md`](00-BUILD-INDEX.md)** — it
sequences the backend build for two devs: Phase 0 (schemas + types, done
together, freezes the contract) then split into two independent modules
(Module A = pipeline + evaluation engine; Module B = work surface + data APIs).
Auth is **deferred** — keep the existing key-match for now (`06-sp1-auth-seats.md`).

Each doc has an **Architecture**, a **Why**, and an **Implementation plan** with
the audit bug IDs mapped in. Recommended order: SP0 → (SP2 + EVAL together,
since EVAL needs the new `roles.evaluation_spec` and `company_persona` tables) →
SP3 + SP4 → SP1 → SP5–7.

The SP0 and SP2 docs are written to implementation-plan depth. SP3/SP4 are solid
designs with a raw-data-first build path. SP1 and hardening are outlines to flesh
out when you reach them.

> **Migration safety:** the rebuild is "aggressive" in end-state but **expand-
> contract** in execution — additive migration, backfill/port script, one short
> freeze for cutover, then contract. You stay live throughout. Details in
> `02-sp2-data-foundation.md §6`.
