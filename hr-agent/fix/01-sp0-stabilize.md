# SP0 — Stabilize (crashes + auto-lane trust)

> **Goal:** stop the bleeding on the *current* schema, before any rebuild.
> Two buckets: (A) confirmed crashes that throw in production, and (B) the
> "auto-lane trust" fixes — because in the new design everything up to assessment
> runs with **no human watching**, so the automated lane must be correct and must
> always communicate.
>
> This SP is intentionally surgical: small diffs, no schema changes, no new
> abstractions. It buys breathing room for SP2.

---

## Why this first

The product decision is "auto until assessment, human after." That means the
automated lane is trusted to reject candidates and send emails *with no human in
the loop*. Today that lane has silent rejections (`P-2`), never sets `HIRED`
(`P-3`), advances stages before the side-effect succeeds (`P-1`), and crashes on
common inputs (`CRASH-1..4`). Shipping the new surface on top of an untrustworthy
lane just makes the untrustworthiness visible faster. Fix the lane first.

---

## Bucket A — Confirmed crashes (throw in prod)

| ID | File:Line | Fix |
|----|-----------|-----|
| `CRASH-1` | `pipeline/v1.py:463` | `red_flags` is `list[str]`, code does `f.description`. Change `[f.description for f in evaluation.red_flags]` → `[f for f in evaluation.red_flags]`. |
| `CRASH-2` | `activities/fit_score.py:~180` | `role.jd_text[:8000]` crashes when JD is null. Use `(role.jd_text or "")[:8000]`. Also guard at the activity entry: if no JD, park the role (do not score). |
| `CRASH-3` | `supervisor/engine.py:292-308` | Supervisor `complete()` missing required `prompt_version`. **The supervisor is being retired** (see SP2 — the action backbone replaces it). For SP0, *disable* the supervisor lifespan task and event subscription so it stops raising/retrying. Do not patch it. |
| `CRASH-4` | `api/webhooks_voice.py:1268-1278` + `v1_evaluate_voice_call.py:205` | Arq serializes `EmotionFeatures` as a dict; evaluator calls `.model_dump()`. Re-hydrate: `EmotionFeatures(**paralinguistic)` inside the Arq job before use. |

## Bucket B — Auto-lane trust (silent failures)

| ID | File:Line | Fix |
|----|-----------|-----|
| `P-2` | `v1_evaluate_voice_call.py:219-234` | Voice reject sets stage `REJECTED` but **never sends a rejection email**. Call `run_rejection(...)` on the reject branch. (Every auto-reject must communicate.) |
| `P-3` | `activities/offer.py` (whole file) | `generate_offer` sends the offer email but **never sets `HIRED`**. Call the new `transition(app, HIRED, ...)` after the offer email succeeds. |
| `P-1` | `v1_dispatch_assessment.py:49-55`, `v1_voice_screening.py:286`, `v1_schedule_meeting.py:268` | Stage set **before** the email/call succeeds. Reorder: perform the side-effect, then set the stage. On failure, do not advance. (This becomes structural in SP2 via `transition()`, but reorder now.) |
| `V-M1` | `classifiers/voicemail.py:98` | Reads `a.get("answer", "")`; answers are stored under `answer_transcript`. Change to `a.get("answer_transcript", "")`. (Voicemail detection is currently a no-op.) |
| `V-M3` | `services/voice_context.py:205-207` | Wrong key names → extracted facts never reach prompts. Fix: `current_ctc`→`current_ctc_lpa`, `expected_ctc`→`expected_ctc_lpa`, `notice_period`→`notice_period_days`, `location`→`current_location`, `experience_years`→`total_experience_years`. |
| `PR-2` | `voice_screening.py` (VOICE_SCREEN_GEN Q5) | Aria says "You're based in None." Guard the location clause: emit it only when location is known (`f"You're based in {loc}. " if loc else ""`). |
| `NB-5` | `v1_evaluate_voice_call.py:259-273` + blank-transcript path 120-143 | `needs_hr_review` verdict relies on the (dead) supervisor for notification. Replace with a **direct** Teams/email alert (mirror `notify_round_complete` in `v1_meeting_analysis.py`). Blank-transcript path sends a "voice call failed — retry needed" alert. |

> `NB-5`'s alert is a stopgap; SP2 replaces it with an `action_item` derived from
> the event. Wire the direct alert now so nobody is silently stuck.

## Voice webhook CAS hardening (data integrity)

These cause duplicate evaluations / permanent stalls. Fix in SP0 if voice volume
is live; otherwise they can ride into SP2.

| ID | Fix |
|----|-----|
| `V-C2` | Add `claim_completion` CAS to `/elevenlabs/recover/{conversation_id}`. |
| `V-C3` | Add `claim_completion` CAS to `webhook_watchdog._try_recover_from_elevenlabs`. |
| `V-C1` | Add an orphaned-sentinel sweep to the watchdog: clear `_in_flight:*` older than 15 min. |
| `V-M5` | Add a `UNIQUE` constraint on `voice_calls.provider_call_id` and handle `MultipleResultsFound`. |

---

## Implementation plan

1. **Branch** `fix/sp0-stabilize` off `development`.
2. **Crashes** (Bucket A) — one commit each, with a regression test that
   reproduces the throw first (TDD). `CRASH-3`: disable the supervisor task in
   the FastAPI lifespan and remove its event subscription; leave the tables for
   SP2 to drop.
3. **Auto-lane trust** (Bucket B) — `P-2`/`P-3` need a small integration test
   asserting an `email_sends` row and the correct terminal stage. `P-1` reorder:
   assert stage is unchanged when the mocked side-effect raises.
4. **Voice CAS** — only if voice is live in prod now.
5. **Verify**: run the existing suite; manually drive one candidate through
   auto-reject (confirm email) and one through offer (confirm `HIRED`).

## Acceptance criteria

- No unguarded `jd_text` / `red_flags` / `model_dump` paths remain (grep + tests).
- An auto-rejected candidate always has a matching `email_sends` row.
- Offer flow lands the application at `HIRED`.
- Supervisor no longer appears in logs (task disabled).
- A failed side-effect never leaves a stage advanced.

## Explicitly out of scope for SP0

New tables, the state machine refactor, the inbox, persona/eval specs. Those are
SP2/EVAL. SP0 is "make today's flow not lie and not crash."
