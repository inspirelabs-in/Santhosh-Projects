# SP3 — The Operational Surface (Split Inbox + Pulse)

> Home becomes a **split view**: a glanceable, AI-ranked **action inbox** on the
> left and **Pulse** (the recruiter agent) on the right. You see what needs you
> and act in the same screen — from the queue *or* by commanding Pulse. The dummy
> dashboard, `/ceo`, `/hr`, `/meetings`, `/assessments`, `/voice-screens` pages
> retire; their content becomes filters on `/candidates` or items in the inbox.
>
> Built on SP2's derived inbox + `domain_events`. **Near-term build focus is the
> raw-data-correct version**; visual polish is SP5–7.

---

## 1. Layout

```
┌ TODAY ─────────────────────┐┌ PULSE ──────────────────────┐
│ Need you now (3)           ││ you: schedule the technical  │
│  ▸ Assessment · Rahul      ││   round for @Rahul Thu 3pm   │
│    [Review][Open]          ││ pulse: confirm?              │
│  ▸ Reschedule · Priya      ││   [confirm card]             │
│    wants Thu 4pm [Approve] ││                              │
│  ▸ Tech analysis · Dev     ││ > _                          │
│    84 [Review][Advance]    ││                              │
│ Waiting (5)  ⌄             ││  (history persisted;         │
│ Scheduled today (2) ⌄      ││   routed at /conversations)  │
└────────────────────────────┘└──────────────────────────────┘
   🔔 notifications bell (FYI feed)        ⌘K command palette
```

- **Left = derived inbox**, grouped: **Need you now** (SLA-breaching / high
  priority), **Waiting** (snoozed / not yet due), **Scheduled today** (interviews
  happening). Each item is an action card.
- **Right = Pulse**, always present, the command surface. The `@mention` already
  embeds the hidden `application_id`; acting from a card can also seed Pulse.
- **🔔 bell** = the FYI `notifications` feed (read/unread), separate from actions.

## 2. The action card

Each card renders from the inbox read model:
- **AI one-liner** — "why this matters" (cached, computed on read).
- **Key facts** — score/verdict/fit, the candidate + role.
- **Primary action** (pre-filled) + **secondary actions** + **Open candidate**.

Action types → actions (all **stage-gated** by SP2's template):

| Action type | Primary | Secondary |
|---|---|---|
| `assessment_review` | Review submission (opens trace at assignment) | Advance · Reject |
| `meeting_scheduling` | Schedule (pre-filled slots) | Pick time · Skip round |
| `reschedule_request` | Approve requested time | Pick another · Open |
| `interview_analysis_ready` | Review analysis | Advance · Reject |
| `decision_pending` | Hire | Reject · Add round |
| `voice_call_failed` | Retry call | Mark handled |
| `candidate_email_reply` | Open thread / draft reply | Mark handled |
| `jd_incomplete` | Finish role / generate spec | — |

## 3. How acting works (two paths, one model)

- **Fast path** — direct typed endpoint for unambiguous actions (approve
  reschedule, mark handled, retry call). Resolves the underlying event
  (`resolved_at`) or triggers `transition()`. Instant.
- **Confirm path** — actions that benefit from review/edit (schedule, custom
  email, reject-with-reason) seed Pulse with a pre-filled command → Pulse's
  existing confirm card. Reuses the recruiter-agent tooling.

Either way the *same* `transition()` + event resolution runs, so the inbox and
the candidate trace stay consistent. There is **one** action model with two entry
points (inbox card and Pulse) — never divergent logic.

## 4. Ranking

`priority = w1·type_severity + w2·sla_breach + w3·fit_tier + w4·wait_time`,
computed in `services/inbox.py`, cached per `action_key`. SLA per type is config
(`config_settings`). AI may re-rank/summarize but the deterministic score is the
floor (so the queue is explainable, not a black box).

## 5. `/conversations/[id]` (fixes the routing + persistence gaps)

- Real routed conversations (the "no `/conversation/id`" gap). List at
  `/conversations`, detail at `/conversations/[id]`.
- **Fix `NB-7`**: `hydrate()` must render `role:"system"` messages (nudge/
  reschedule cards) on reload — they're in the DB, just dropped by the client.
- Nudge routing hardening (from `meeting-scheduling.md §6`): bump
  `conversation.updated_at` on nudge; per-recruiter routing once seats exist.

## 6. Retiring the dummy pages

| Old page | Becomes |
|---|---|
| `/dashboard` | `/` (Split Inbox + Pulse) |
| `/ceo`, `/hr` (+ `[id]`) | inbox items + role-aware sections of the candidate workspace (kills `NB-11`) |
| `/meetings` | saved filter on `/candidates` (stage type = interview) + "Scheduled today" group |
| `/assessments` | saved filter (stage type = assignment/assessment_review) |
| `/voice-screens` | saved filter (stage type = voice_screen) |

## 7. Implementation plan (raw-data-first)

1. Backend `GET /inbox` returning the derived read model (action_key, type,
   facts, suggested action) + `POST /inbox/{action_key}/{snooze|dismiss|assign}`
   writing `action_overlay`.
2. Direct action endpoints (approve-reschedule, mark-handled, retry-call) calling
   `transition()` / `resolve_event()`.
3. Frontend `/` split layout: inbox list (grouped, real data, real actions) +
   existing Pulse panel docked right. **Function over polish** for now.
4. `/conversations/[id]` route + fix `hydrate()` for `system` messages.
5. Replace dummy pages with filters; delete `/ceo/[id]`,`/hr/[id]` duplication.
6. Wire SSE: inbox subscribes to the org's Redis push for live updates.

## Acceptance criteria

- The inbox shows exactly the human-gate work + open exceptions, live-updating,
  surviving reload.
- Acting from a card and the same action via Pulse produce identical state.
- No item can be acted on out of stage (gating).
- The retired pages 404/redirect; no redundant endpoints remain.
