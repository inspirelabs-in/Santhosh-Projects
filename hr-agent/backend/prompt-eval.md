# Pulse Recruiter Agent — Complete Tool Inventory

## Source: 2026-06-28 audit

**Schema registration:** `src/recruiter_agent/schemas.py` — `RECRUITER_TOOLS` list (line 7–644), plus `give_choice` appended at line 653.

**Runtime dispatch:** `src/recruiter_agent/tools.py` — `TOOLS` dict (line 2039–2081), invoked via `call_tool()` (line 2087).

**LLM receives all schemas** at `runner.py:930` and `runner.py:956` as `tools=RECRUITER_TOOLS`.

---

### ❌ DEAD — registered in TOOLS dict / codebase, zero callers

| Function | File:Line | Why dead | Keep? |
|----------|-----------|----------|-------|
| `ensure_role_assignment()` | `tools.py:641` | Wraps `generate_assignment_for_role` with retry + audit. No callers anywhere. | Probably drop |
| `uuid_zero()` | `tools.py:769` | Returns null UUID `00000000-...`. No callers. | Drop |
| `gen_tailored_questions()` | `generators.py:64` | Commented `[SCRAPE] dead`. Was Chat-V2 candidate screening. No callers. | Drop |
| `extract_turn()` | `generators.py:162` | Commented `[SCRAPE] dead`. Was Chat-V2. No callers. | Drop |
| `EXTRACT_TURN_V1` prompt | `prompts/extract.py` | Only consumed by dead `extract_turn()` | Drop |
| `TAILORED_QS_V1` prompt | `prompts/tailored_qs.py` | Only consumed by dead `gen_tailored_questions()` | Drop |

### ❌ BUG — prompt references a tool that doesn't exist

| "Tool" | Prompt location | Problem |
|--------|-----------------|---------|
| `trigger_chat_invite` | `prompts.py:193` (text: *"Call trigger_chat_invite(application_id) — Confirm card"*), `prompts.py:237` (in confirm-gated list), `prompts.py:258` (slash shortcut `/invite`) | **No schema in `schemas.py`**, **no handler in `tools.py`**, **no TOOLS entry**, **no RBAC entry**. LLM will call it → runtime `"unknown_tool"` error. |

---

### ✅ ACTIVE — 37 tools in TOOLS dict (LLM-callable)

#### READS — no confirmation required, viewer+ role

| # | Tool name | Schema loc | Handler loc | RBAC | Confirm? | Listed in prompt text? |
|---|-----------|------------|-------------|------|----------|------------------------|
| 1 | `list_candidates` | `schemas.py:7` | `tools.py:55` | viewer | No | Yes |
| 2 | `get_candidate` | `schemas.py:31` | `tools.py:94` | viewer | No | Yes |
| 3 | `list_roles` | `schemas.py:45` | `tools.py:127` | viewer | No | Yes |
| 4 | `get_role` | `schemas.py:60` | `tools.py:148` | viewer | No | **No** |
| 5 | `pipeline_metrics` | `schemas.py:74` | `tools.py:175` | viewer | No | Yes |
| 6 | `stuck_applications` | `schemas.py:82` | `tools.py:189` | viewer | No | Yes |
| 7 | `audit_tail` | `schemas.py:97` | `tools.py:214` | viewer | No | Yes |
| 8 | `search_talent_pool` | `schemas.py:112` | `tools.py:945` | **defaults to recruiter** (gap — should be viewer) | No | **No** |
| 9 | `search_candidates` | `schemas.py:128` | `tools.py:237` | viewer | No | Yes |
| 10 | `get_journey_report` | `schemas.py:389` | `tools.py:264` | viewer | No | Yes |
| 11 | `metrics_period` | `schemas.py:401` | `tools.py:290` | viewer | No | Yes |
| 12 | `list_meetings` | `schemas.py:413` | `tools.py:313` | viewer | No | Yes |
| 13 | `list_voice_calls` | `schemas.py:425` | `tools.py:336` | viewer | No | Yes |
| 14 | `read_audit` | `schemas.py:437` | `tools.py:359` | viewer | No | Yes |
| 15 | `recall` | `schemas.py:584` | `tools.py:747` | viewer | No | Yes |
| 16 | `smart_defaults_for_role` | `schemas.py:572` | `tools.py:852` | viewer | No | Yes |
| 17 | `parse_attachment` | `schemas.py:461` | `tools.py:781` | **recruiter** (prompt says read tool, RBAC says recruiter — inconsistency) | No | Yes |
| 18 | `propose_slots` | `schemas.py:281` | `tools.py:466` | recruiter | No | Yes |
| 19 | `suggest_meeting_slots` | `schemas.py:293` | `tools.py:494` | viewer | No | **No** |

#### WRITES (confirm-gated unless noted) — recruiter+ role

| # | Tool name | Schema loc | Handler loc | RBAC | Confirm? | Listed in prompt text? |
|---|-----------|------------|-------------|------|----------|------------------------|
| 20 | `create_role` | `schemas.py:141` | `tools.py:395` | recruiter | Yes | Yes |
| 21 | `create_role_with_assignment` | `schemas.py:489` | `tools.py:340` | recruiter | Yes | **No** |
| 22 | `update_role` | `schemas.py:169` | `tools.py:553` | recruiter | Yes | Yes |
| 23 | `archive_role` | `schemas.py:192` | `tools.py:623` | recruiter | Yes | Yes |
| 24 | `set_role_assignment_brief` | `schemas.py:204` | `tools.py:676` | recruiter | Yes | Yes |
| 25 | `override_stage` | `schemas.py:221` | `tools.py:704` | recruiter | Yes | Yes |
| 26 | `send_custom_email` | `schemas.py:237` | `tools.py:482` | recruiter | Yes | Yes |
| 27 | `add_candidate_note` | `schemas.py:253` | `tools.py:522` | recruiter | No (low-risk) | Yes |
| 28 | `schedule_interview` | `schemas.py:265` | `tools.py:547` | recruiter | Yes | Yes |
| 29 | `schedule_meeting` | `schemas.py:308` | `tools.py:576` | recruiter | Yes | Yes |
| 30 | `reschedule_meeting` | `schemas.py:326` | `tools.py:613` | recruiter | Yes | **No** |
| 31 | `set_panel_member` | `schemas.py:345` | `tools.py:527` | recruiter | Yes | Yes |
| 32 | `add_panel_member` | `schemas.py:361` | `tools.py:465` | **defaults to recruiter** (gap — should be explicit) | No | **No** |
| 33 | `remember` | `schemas.py:473` | `tools.py:729` | recruiter | No | Yes |
| 34 | `update_setting` | `schemas.py:449` | `tools.py:825` | **admin** | Yes | Yes |
| 35 | `publish_linkedin_post` | `schemas.py:557` | `tools.py:657` | recruiter | Yes | Yes |

#### DRAFT / PANEL TOOLS — surface own UI, not confirm-gated in backend

| # | Tool name | Schema loc | Handler loc | RBAC | Confirm? | Listed in prompt text? |
|---|-----------|------------|-------------|------|----------|------------------------|
| 36 | `propose_role_draft` | `schemas.py:596` | `tools.py:1956` | **defaults to recruiter** (gap — should be explicit) | No (panel UI) | Yes (draft tool) |
| 37 | `generate_assignment_for_role` | `schemas.py:521` | `tools.py:429` | recruiter | No (panel UI) | Yes (draft tool) |
| 38 | `draft_linkedin_post` | `schemas.py:540` | `tools.py:634` | recruiter | No (draft) | Yes (draft tool) |

#### UI-ONLY — never executed on backend

| "Tool" | Schema loc | Backend | Notes |
|--------|------------|---------|-------|
| 39 | `give_choice` | `schemas.py:653` (appended) | `call_tool()` returns `"error"` — frontend intercepts |

---

### Summary of Issues

#### Prompt-text gaps (tool exists in TOOLS + schemas but LLM isn't told about it in prompt text)
- `get_role` (#4)
- `search_talent_pool` (#8)
- `create_role_with_assignment` (#21)
- `suggest_meeting_slots` (#19)
- `reschedule_meeting` (#30)
- `add_panel_member` (#32)

#### RBAC gaps (tool missing from `rbac.py:TOOL_ROLES`, defaults to "recruiter")
- `search_talent_pool` (#8) — should be **viewer** (read tool)
- `propose_role_draft` (#36) — fine as recruiter, should be explicit
- `add_panel_member` (#32) — fine as recruiter, should be explicit

#### RBAC inconsistency
- `parse_attachment` (#17) — prompt lists it as a read tool, but RBAC says **recruiter**

#### Dead code
- `ensure_role_assignment()` in `tools.py:641`
- `uuid_zero()` in `tools.py:769`
- `gen_tailored_questions()` + `extract_turn()` in `generators.py`
- Corresponding dead prompts: `EXTRACT_TURN_V1`, `TAILORED_QS_V1`

#### Prompt bug
- `trigger_chat_invite` — referenced in 3 places in `prompts.py` (line 193 as tool call, line 237 in confirm-gated list, line 258 as slash shortcut `/invite`) but has **no schema, no handler, no TOOLS entry, no RBAC entry**
