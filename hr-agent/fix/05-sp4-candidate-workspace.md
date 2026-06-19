# SP4 — Candidate Workspace (hybrid trace + jump-rail)

> `/candidate/[id]` must keep **every trace** but stop being a 60K-char monolith
> served by one endpoint joining 6+ tables (`NB-9`). The new workspace: a compact
> sticky header (verdict + stage-gated actions), a **chronological trace stream**
> as the spine, and a **sticky jump-rail** of section anchors. Sub-resources load
> lazily. This is also where the **raw data you said is buried gets surfaced**.

---

## 1. Layout

```
┌ Rahul Sharma · Senior Backend Eng ───────────── [stage: assessment review] ┐
│ fit 78 green · voice 82 · 24→30 LPA · 60d · Bangalore  [Advance][Reject][⋯] │
├──────────────────────────────────────────┬──────────────────────────────────┤
│ TRACE  [all|scores|comms|interviews|notes]│  JUMP RAIL                       │
│ ● Assignment submitted · 3 files   [▾]    │   • Profile / Resume             │
│   └ preview: src/, README.md  ← NB-8      │   • Screening                    │
│ ● Voice evaluated · 82 clear_pass  [▾]    │   • Voice  ←                     │
│   └ transcript · ▶ recording              │   • Assignment                   │
│ ● Screening evaluated · clear_pass [▾]    │   • Interviews                   │
│ ● Fit scored · 78 green · breakdown[▾]    │   • Emails                       │
│ ● Applied · careers form                  │   • Audit (full trace)           │
└──────────────────────────────────────────┴──────────────────────────────────┘
```

- **Header** — identity, key extracted facts, current stage, and the *same*
  stage-gated actions that appear on the inbox card (one action model).
- **Trace stream** — every `domain_event` for the application as a rich,
  expandable card, in time order, filterable by lens.
- **Jump rail** — sticky anchors that scroll-jump into the stream and trigger the
  matching sub-resource fetch.

## 2. Surface the buried raw data (your explicit ask)

Each trace card expands to the real artifact, fetched lazily:

| Card | Expands to | Fixes |
|---|---|---|
| Assignment submitted | file list **+ `extracted_text_preview`** with code highlighting; links; deployed URL | `NB-8` (preview exists, never shown) |
| Voice evaluated | per-dimension scores + **evidence/reasoning** (EVAL), transcript, **▶ recording playback** | — |
| Screening evaluated | Q&A verbatim + per-question scores + logistics | — |
| Fit scored | per-dimension breakdown + evidence (EVAL) | `PR-4` (no more "n/a") |
| Interview analysis | scores + emotion timeline + summary + transcript | `PR-11` (full transcript, chunked) |
| Emails | every `email_sends` + inbound replies, threaded | the "did we even email them" gap |
| Audit | the complete `domain_events` + `audit_log` trace — nothing hidden | "keep all traces" |

## 3. Lazy sub-resources (kill the monolith)

Split the one mega-endpoint into:
```
GET /candidates/{id}            # header + lightweight summary only
GET /candidates/{id}/trace      # the event stream (paginated)
GET /candidates/{id}/profile
GET /candidates/{id}/screening
GET /candidates/{id}/voice
GET /candidates/{id}/assignment
GET /candidates/{id}/interviews
GET /candidates/{id}/emails
GET /candidates/{id}/audit
```
Frontend fetches the header + trace first; section payloads load on
expand/jump. Mutating one section invalidates only its key (SWR), not everything.

## 4. Role-aware sections (kills `/ceo`,`/hr` duplication)

The same workspace serves CEO/HR — it just emphasizes the relevant slice (CEO
sees the brief + prior rounds; HR sees logistics + offer). No separate pages, no
shared-endpoint confusion (`NB-11`).

## 5. `/candidates` list (the real one)

Replaces the dummy lists. Table/board with filters (role, stage type, tier,
source, stalled) and the saved lenses that retired the meetings/assessments/
voice pages. Each row → the workspace.

## 6. Implementation plan (raw-data-first)

1. Backend: split endpoints (§3); `/trace` reads `domain_events` joined to
   artifacts; each sub-resource returns its real blob (including the previously
   hidden `extracted_text_preview` and recording URLs).
2. Frontend: new workspace shell (header + stream + rail); migrate the existing
   rich renderers out of the 60K monolith into per-card components; lazy fetch on
   expand. **Prioritize showing real data correctly over visual polish.**
3. `/candidates` list with filters + saved lenses.
4. Delete the monolith page and `/ceo/[id]`,`/hr/[id]`.

## Acceptance criteria

- Every artifact (assignment text, transcript, recording, eval reasoning, emails,
  full audit) is reachable in the workspace — nothing requires a download to read.
- Initial load fetches header + trace only; sections lazy-load.
- One candidate page serves recruiter/CEO/HR via emphasis, not separate routes.
