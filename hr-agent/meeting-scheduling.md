# Meeting Scheduling — Technical Reference

How interview meetings get booked, how the suggested times are computed, and
how a candidate's reschedule request is routed back into the HR (Pulse) chat.

Scheduling is **chat-driven**: recruiters book and move meetings by talking to
the Pulse recruiter agent. The legacy auto panel-availability / smart-scheduler
flow is disabled (see `_fire_meeting` in `services/auto_progress.py`).

---

## 1. Components

| Concern | File |
|---|---|
| Pulse tools (`schedule_meeting`, `reschedule_meeting`, `suggest_meeting_slots`) | `backend/src/recruiter_agent/tools.py` |
| Tool schemas (LLM-facing) | `backend/src/recruiter_agent/schemas.py` |
| Tool RBAC + confirm gating | `backend/src/recruiter_agent/rbac.py` |
| Agent turn loop + confirm-card preview | `backend/src/recruiter_agent/runner.py` (`_propose_preview`) |
| **Core booking / reschedule logic** | `backend/src/services/chat_meeting.py` |
| Provider facade (gmeet / teams) | `backend/src/services/online_meeting.py` |
| Google Calendar + Meet client | `backend/src/services/gmeet_meeting.py` |
| Slot/time suggestion | `backend/src/services/slot_suggest.py` |
| Meeting persistence | `backend/src/db/repositories/meeting_session.py` (`MeetingSession`) |
| Candidate reschedule endpoint | `backend/src/api/meeting_reschedule.py` |
| Candidate reschedule page | `frontend/src/app/meeting/reschedule/[token]/page.tsx` |
| Same-origin proxy for that page | `frontend/src/app/api/meeting-reschedule/[token]/route.ts` |
| Event bus (Redis pubsub) | `backend/src/services/events.py` (`publish_event`) |
| HR-chat nudge worker | `backend/src/services/recruiter_nudge_worker.py` |
| Reschedule card (HR chat) | `frontend/src/components/recruiter-chat/Attachments.tsx` (`RescheduleRequestCard`) |
| Shared date/time picker | `frontend/src/components/quick-datetime.tsx` |

**Provider:** resolved by `settings.online_meeting_provider` (`gmeet` | `graph`).
For Google Meet, create/reschedule emit native Google Calendar invites
automatically; we also send a branded email carrying the reschedule link.
Requires `ONLINE_MEETING_PROVIDER=gmeet` + a bootstrapped Google OAuth token.

---

## 2. Booking flow (recruiter → candidate)

```
Recruiter types in Pulse  ──►  schedule_meeting tool  ──►  confirm card  ──►  book_meeting()
   (@mention embeds the          (confirm-gated:                                │
    application_id at send)        rbac.CONFIRM_REQUIRED)                        ▼
                                                              create_online_meeting() → Google Meet event
                                                              persist MeetingSession + set_stage()
                                                              email candidate + panel (with reschedule link)
                                                              enqueue Read.ai bot
```

1. The recruiter mentions a candidate with `@`. On send, the frontend attaches
   the hidden `(application_id: <uuid>)` to the message text (never shown in the
   bubble) so Pulse can pass it straight to the tool.
2. Pulse calls `schedule_meeting(application_id, round, scheduled_at, panel_emails, duration_minutes)`.
3. It is **confirm-gated** (`rbac.CONFIRM_REQUIRED`). The runner emits a
   confirm card; `_propose_preview()` renders a human sentence (no raw JSON,
   no application_id), e.g. *"Schedule a technical interview at … with panel: …"*.
4. On confirm, `chat_meeting.book_meeting()`:
   - validates round (`technical|ceo|hr`), future time, non-empty panel;
   - `online_meeting.create_online_meeting()` → Google Calendar event with an
     auto-attached Meet link; Google emails attendees the calendar invite;
   - persists a `MeetingSession` row: `round`, `teams_join_url` = Meet link,
     `scheduled_at`, `bot_id` = **calendar event id**, `bot_provider`,
     `negotiation_state` = `{panel_emails, duration_minutes, organiser, provider}`;
   - `set_stage()` → `TECHNICAL|CEO|HR_MEETING_SCHEDULED`;
   - sends branded `meeting_invite_candidate` / `meeting_invite_panel` emails,
     the candidate one carrying a signed **reschedule link**;
   - `enqueue("dispatch_meeting_bot", …)` (best-effort) so Read.ai joins;
   - writes an audit row `meeting_scheduled_via_chat`.

---

## 3. How the timings are computed

`slot_suggest.suggest_slots()` — a **heuristic**, not calendar-aware:

- walks the next `business_days` (default **5**) **business days**, skipping
  Saturday and Sunday;
- for each day proposes fixed local hours — default **11:00 and 15:00** in
  `Asia/Kolkata` (IST);
- drops any slot sooner than `min_lead_hours` (default **12h**) from now;
- returns up to `limit` (default 5–6) start times as tz-aware **UTC** datetimes.

`format_slot()` renders each as `"Mon 23 Jun, 3:00 PM IST"`.

> ⚠️ It does **not** check Google free/busy — there is no real availability
> lookup. These are sensible business-hour options only. The recruiter (or the
> candidate) picks one; nothing guarantees the panel is actually free.

Custom times (candidate page + HR card) come from `QuickDateTime`
(`<input type=date>` + 30-min time `<select>`), which builds a local datetime
and emits it as a UTC ISO string.

---

## 4. Reschedule flow (candidate → HR chat → re-book)

This is the part where "the HR chat gets triggered again."

```
Candidate email ── reschedule link (signed JWT) ──► /meeting/reschedule/{token} (Next page)
        │                                                   │  GET (via same-origin proxy → backend)
        │                                                   ▼
        │                                      shows current time + suggested slots
        ▼  picks a time / custom / reason, POSTs
backend POST /meeting/reschedule/{token}
        ├─ verify token → (meeting_session_id, application_id)
        ├─ store request on MeetingSession.negotiation_state["reschedule_requested"]
        ├─ log_audit("meeting_reschedule_requested", actor="candidate")   → notifications bell
        └─ publish_event(application_id, "meeting_reschedule_requested", {ms_id, round, requested_at, reason})
                       │  Redis channel: hiring-agent:application:{app_id}
                       ▼
recruiter_nudge_worker  (backend lifespan task, psubscribed to hiring-agent:application:*)
        ├─ _format_nudge(): loads current scheduled time, builds suggested slots,
        │                    assembles a "reschedule-request" attachment + text summary
        ├─ _pick_active_conversation(): most-recently-active Pulse conversation (24h, nudges enabled)
        └─ _push_nudge(): persist a role="system" message + publish to recruiter-chat:{conv}:nudge
                       │
                       ▼
HR's open SSE stream (api/recruiter_chat._sse_loop) → emits an "attachment" event (_nudge:true)
                       ▼
Frontend renders RescheduleRequestCard  ──► recruiter clicks a time
                       ▼
ctx.dispatch("Reschedule the <round> interview (meeting_session_id: X) to <ISO>. Keep the same panel.")
                       ▼
Pulse → reschedule_meeting tool → confirm card → chat_meeting.reschedule_meeting()
        ├─ reschedule_online_meeting(): gmeet PATCHes the event in place (same Meet link);
        │                               teams cancels + recreates
        ├─ update MeetingSession (scheduled_at, join_url, re-arm bot)
        ├─ re-send branded invites to candidate + panel
        └─ audit "meeting_rescheduled_via_chat"
```

### The reschedule link / token
- Built by `chat_meeting.build_reschedule_link()` →
  `{frontend_base_url}/meeting/reschedule/{token}`.
- Token is a **HS256 JWT** (`jwt_signing_secret`, issuer `jwt_issuer`), 14-day
  expiry, payload `{ms_id, app_id, act:"meeting_reschedule"}`.
- Verified by `verify_reschedule_token()`.

### Why the candidate page calls a same-origin proxy
The page is opened over a public HTTPS origin (ngrok). A direct browser call to
the backend would fail (mixed-content + unreachable localhost), so the page hits
`/api/meeting-reschedule/{token}` (a Next route handler) which proxies to the
backend **server-side** over the Docker network (`BACKEND_INTERNAL_URL`,
default `http://backend:8000`).

### The HR-chat card payload (`reschedule-request` attachment `data`)
```jsonc
{
  "application_id": "...",
  "meeting_session_id": "...",
  "round": "technical",
  "candidate_name": "...",
  "role_title": "...",
  "current_scheduled_at": "ISO|null",
  "current_label": "Fri 19 Jun, 2:00 PM IST|null",
  "requested_at": "ISO|null",            // candidate's preferred time
  "requested_label": "Thu 25 Jun, 4:00 PM IST|null",
  "reason": "...|null",
  "suggested_slots": [{ "scheduled_at": "ISO", "label": "..." }]
}
```
The card shows the candidate's ★ requested time first, then suggested slots,
plus a custom `QuickDateTime`. Each button `dispatch`es a precise instruction
to Pulse, which maps it to `reschedule_meeting(meeting_session_id, new_scheduled_at)`.

---

## 5. Pipeline stage transitions

`MeetingSession.round` ∈ `technical | ceo | hr`. Booking sets:
`TECHNICAL_MEETING_SCHEDULED | CEO_MEETING_SCHEDULED | HR_MEETING_SCHEDULED`
(`chat_meeting._ROUND_TO_STAGE`). When the pipeline reaches a meeting round on
its own, `auto_progress._fire_meeting()` does **not** auto-book — it parks the
candidate at `NEEDS_HR_REVIEW` and publishes `meeting_scheduling_needed`, which
nudges the recruiter to schedule via chat.

---

## 6. Known limitations / flaws

- **No real availability check.** `suggest_slots()` is pure heuristic; it never
  queries Google free/busy for the panel.
- **Nudge routing is dev-grade.** `_pick_active_conversation()` picks the most
  recently-active conversation across *all* recruiters (no per-recruiter
  routing), and only if one was active in the last 24h — otherwise the live
  nudge is dropped (the notifications-bell audit entry still records it).
- **Nudge does not bump `conversation.updated_at`**, so a reschedule card can
  land in a chat that stays lower in the sidebar instead of jumping to the top.
- **Reschedule is a 2-step** for the recruiter: click a time on the card →
  confirm the Pulse confirm-card.
- **Read.ai bot re-arm on reschedule** is best-effort; for gmeet the calendar
  event moves in place so the bot follows the invite.
- The agent occasionally emits the `schedule_meeting` confirm card more than
  once per request; stray duplicate cards must be cancelled to avoid double
  booking (no idempotency guard yet).
