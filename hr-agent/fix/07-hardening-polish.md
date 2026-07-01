# SP5–7 — Hardening & Polish

> **STATUS (2026-06-22): PARTIAL / LOW.** Voice idempotency guard (processing_status CAS, V-C1/2/3, V-M5) shipped. Remaining: Langfuse resilience, CEO-brief guards, design polish -- all LOW, tracked in `architecture.md` Progress Tracker.

> **STATUS: LAST.** Outlines to flesh out after the foundation, surface, and
> evaluation work land. Grouped by area. Each item carries its audit ID.

---

## SP5 — Scheduling hardening

The chat-driven scheduling works but has real-availability and duplication gaps.

| ID | Fix |
|----|-----|
| `MS-H2` | Graph free/busy uses a random panel member as organiser → always fails silently. Use a service account (or per-member delegated access) for free/busy lookup. |
| `MS-H1` | `_existing_meetings_for_panel` blocks ALL panels globally. Add per-attendee filtering. |
| Slot suggest | `suggest_slots()` is a pure heuristic (no calendar check). Once `MS-H2` works, intersect with real free/busy. |
| `NB-1`/`NB-2` | `/agentic/technical/approve` → `_kick_schedule_meeting` enqueues the OLD `v1_schedule_meeting` path, bypassing idempotent `book_meeting`. Route the approval path through `book_meeting`. |
| `MS-H3`/`MS-H4` | Rejected candidate can still self-book / be manually booked. Re-read `current_stage` in `record_candidate_selection` and `manual_schedule_meeting`. (SP2 gating largely fixes this.) |
| `MS-H5` | Read.ai webhook accepts unauthenticated payloads when secret unset → raise 401 instead of no-op. |
| `MS-H6` | Time-window transcript match assigns to wrong candidate. Prefer `meeting_url`; never fall back to a 30-min window across candidates. |
| Nudge routing | Bump `conversation.updated_at` on nudge; per-recruiter routing once seats exist (`meeting-scheduling.md §6`). |

## SP6 — Pulse (recruiter agent) state-machine bugs

| ID | Fix |
|----|-----|
| `G-1` | Cancel re-triggers the same action — `_build_history_for_llm` strips the pending-confirm pair. Inject a synthetic "proposed → cancelled" pair so the model sees its own rejected proposal. |
| `G-2` | `edited_args` injection — frontend can inject arbitrary keys (e.g. skip screening). Validate against the tool's parameter schema, strip `_`-prefixed keys, re-run RBAC on mutated args. |
| `G-4` | Multi-tool batch with one confirm breaks history (orphaned tool_call). Pair every tool_call with a result row. |
| `G-5`/`G-6`/`G-7` | Confirm-key deleted before execution; expired confirm falls into LLM loop; last hop doesn't enforce `tool_choice="none"`. Harden the confirm lifecycle. |
| `NB-7` | (also in SP3) `hydrate()` drops `role:"system"` messages on reload. Render them. |

## SP7 — Visual / design system

The structural work (SP2–4) makes the UI *correct*; this makes it *good*.

- **Design tokens**: a real palette, spacing scale, typography. Replace the
  ad-hoc styling. Define light/dark.
- **Chat rendering**: proper message/card components for Pulse and the candidate
  chat — consistent bubbles, attachments, confirm cards, code blocks.
- **Component library**: action card, trace card, stage chip, score/confidence
  badge, slot picker — shared across inbox, workspace, and Pulse so they look
  like one product.
- **Empty/loading/error states** everywhere (the lazy sub-resources need them).
- **Responsive**: the Split Inbox + Pulse needs a sensible small-screen fallback
  (stack, or Pulse as a slide-over).

## LLM infrastructure (fold in opportunistically)

| ID | Fix |
|----|-----|
| `LF-H1` | Langfuse one-shot init: if unreachable at start, prompts fall back forever. Add retry + backoff. |
| `LF-H2` | Per-worker in-memory prompt cache → mixed versions. Use a shared Redis-backed cache. |
| `LF-M2` | Duplicate Langfuse traces from double callback registration. |

## Sequencing

SP5 and SP6 are independent and can be picked up by either module owner once the
foundation lands. SP7 should follow SP3/SP4 so it polishes real surfaces, not
moving targets.
