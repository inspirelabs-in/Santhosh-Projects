# HR Agent

AI-powered recruitment automation platform. Handles the full hiring pipeline from
application intake through screening, voice interviews, technical assessments,
panel scheduling, and offer management.

**HR-in-the-loop**: the agent drafts and routes; humans decide. Only clear-pass
candidates auto-advance. Everything else lands in `needs_hr_review`.

---

## Features

### Intake & Parsing
- **Email ingestion** — IMAP polling (Gmail/Outlook) + `email_filter` deterministic classification (application / referral / internship / vendor / unknown)
- **Careers form** — Webhook intake with role matching via `classify_email`
- **Resume parsing** — PDF extraction + LLM-powered profile structuring with candidate merge dedup
- **Intake dedup** — `(candidate_id, role_id)` scoped — same candidate applying to 2 roles = 2 independent pipelines

### Candidate Scoring
- **Fit scoring** — Candidate-vs-JD with tier-based scoring (green / amber / red), configurable weights (skills 50%, experience 25%, CTC 15%, logistics 10%), pending-verification workflow
- **Screening** — Auto-generated questions (multiple-choice, coding, descriptive, voice) via `screening_gen`, structured LLM evaluation via `screening_eval`

### Assessments
- **Timed assignments** — Auto-dispatched with problem doc attachment, deadline tracking
- **Assignment parsing** — GitHub + Loom + deployed URL enrichment, LLM extraction of quality signals
- **Assignment review** — Human-only gate — LLM extracts `completeness`, `quality_signals`, `highlights`, `concerns`; never scores. HR decides pass/reject.

### Voice & Meetings
- **Voice screening** — ElevenLabs ConvAI phone interviews, Gemini audio analysis for paralinguistic evaluation (engaged / neutral / unclear)
- **Meeting scheduling** — Panel member picks 3 slots → candidate picks one → Google Meet created, calendar invites sent
- **Meeting analysis** — Recall.ai / Read.ai bot attends, structured transcript analysis generated
- **Round-to-panel mapping** — Configurable round types per role (technical / CEO / HR / custom)

### Pulse Recruiter Agent
- **Conversational role drafting** — Recruiter describes the role in chat → `propose_role_draft` tool auto-completes title, JD, CTC, location, evaluation criteria from conversation context
- **Artifact Panel** — Side-panel UI for inline editing of draft fields before publishing
- **RBAC-scoped tools** — Permissions for `create_role`, `edit_prompt`, `override_stage`, `extend_offer`
- **Assessment evaluation** — `evaluate_assignment` tool with `RubricCriterion` scoring, always returns `NEEDS_REVIEW`

### V2 Candidate Chat (LangGraph)
- **State machine** — LangGraph state with structured transition planning
- **Assignment flow** — Candidate selects project, submits links, LLM extracts signals, `complete_assignment_submission` parks at `NEEDS_REVIEW`
- **Journey report** — LLM-generated candidate timeline with evidence anchors

### Human Decisions
- **Admin review panel** — Pass/reject with notes for assignment review, tech review
- **HR decision endpoint** — `POST /candidates/{id}/hr-decision` with actions: `extend_offer`, `hire`, `reject`
- **Offer stage** — Manual gate — no offer email auto-sent. Recruiter triggers via Pulse agent or dashboard
- **Audit trail** — Append-only immutable audit log (PostgreSQL trigger)

---

## Pipeline (V2 Stage Keys)

```
intake → parse → fit_score → screening → voice_screen → assignment
  → tech_interview → ceo_interview → offer → hired / rejected
```

| Stage Key | Type | Mode | Description |
|-----------|------|------|-------------|
| `intake` | `intake` | auto | Ingest application, acknowledge |
| `parse` | `parse` | auto | Resume parsing + profile extraction |
| `fit_score` | `fit_score` | auto | Candidate-vs-JD scoring |
| `screening` | `screening` | auto | LLM question gen + eval |
| `voice_screen` | `voice_screen` | auto | ElevenLabs call + Gemini analysis |
| `assignment` | `assignment` | manual | Timed tech assessment + human review |
| `tech_interview` | `interview` | manual | Panel interview + meeting analysis |
| `ceo_interview` | `interview` | manual | CEO round + structured report |
| `offer` | `offer` | manual | HR extends offer manually |
| `hired` | `hired` | auto | Final state |
| `rejected` | `rejected` | auto | Final state |

**Auto-progression**: `stage_runner.py` checks gate conditions per stage type. Clear-pass resumes advance automatically. Stages with `mode: manual` park at `NEEDS_REVIEW` for human decision.

---

## Backend Architecture

```
backend/src/
├── activities/          Temporal activity functions (one per pipeline stage)
│   ├── classify.py      Deterministic email classification
│   ├── fit_score.py     Tier-based candidate-vs-JD scoring
│   ├── intake.py        Application intake with dedup
│   └── v1_*.py          V1 compatibility activities (delegates to V2 services)
├── agent/               V2 LangGraph candidate chat agent
│   ├── generators.py    Tool call generators
│   ├── schemas.py       Pydantic models (RubricCriterion, etc.)
│   └── prompts/         Agent system prompts
├── api/                 FastAPI route handlers
│   ├── apply.py         Application intake endpoint
│   ├── recruiter_chat.py Pulse agent chat API
│   ├── roles.py         Role CRUD
│   ├── v1_dashboard.py  HR decision + admin dashboard
│   └── webhooks.py      Careers form + email webhooks
├── channels/            Email, SMS, Teams, WhatsApp + Jinja2 templates
├── db/                  SQLAlchemy models, repositories, Alembic migrations
│   ├── base.py          ORM models (Application, Candidate, Role, etc.)
│   └── repositories/    Repository pattern (role, application, organization)
├── llm/                 LLM integration layer
│   ├── client.py        Unified invoke with retry/fallback
│   ├── model_registry.py Stage→model mapping (gpt-4o, gpt-4o-mini, claude)
│   ├── prompt_manager.py Centralized PromptRegistry with versioned prompts
│   └── prompts/         Versioned prompt templates per activity
├── models/              Pydantic schemas (events, artifacts, pipelines, etc.)
├── pipeline/            V1 pipeline orchestrator (deprecated, see V2)
├── recruiter_agent/     Pulse recruiter agent
│   ├── tools.py         Tool definitions (propose_role_draft, evaluate_assignment)
│   ├── runner.py        Conversation loop with tool dispatch
│   ├── schemas.py       Tool I/O schemas
│   └── prompts.py       System prompt (v10)
├── services/            Business logic layer
│   ├── pipeline_engine.py V2 transition planning engine
│   ├── stage_runner.py  Generic stage runner with gate evaluation
│   ├── evaluation.py    Gemini audio analysis pipeline
│   ├── mail_ingest.py   IMAP email polling + classification
│   └── smart_scheduler.py Calendar slot management
├── tools/               Tool registry
│   └── registry.py      Model-for-tool resolution
└── workers/             Arq background job definitions
```

### Key Patterns

| Pattern | Description |
|---------|-------------|
| `session_scope()` | Async DB session context manager, used everywhere |
| `log_audit()` | Append-only immutable audit trail |
| `auto_progress` | Automatic stage advancement with gate evaluation |
| `model_for(Stage)` | Maps pipeline stage → LLM model ID |
| `PromptManager.get_prompt()` | Versioned prompt loading with registry |
| `call_tool()` | Pulse agent tool dispatch with `conversation_id` context |
| `_DraftCore` | Flat scalar model for LLM role draft generation (avoids nested model failures) |

---

## V2 State Management

### Pipeline Engine (`pipeline_engine.py`)
- `plan_transition(application, new_stage_key)` → determines `StageAction` (advance / park / needs_review / reject)
- `_GATE_ACTIONS` maps each `StageType` → action strategy
- `_AUTO_ADVANCEABLE_GATES` — stages that auto-pass (intake, parse)
- `NEEDS_REVIEW` — stages that require human decision (assignment, interview, offer)

### Stage Runner (`stage_runner.py`)
- `run_stage(stage_key, application)` — generic runner dispatched by stage type
- Gate functions: `complete_assignment_submission()` → always `NEEDS_REVIEW`
- Evaluation models: `eval_assignment()`, `eval_voice_screen()` → structured LLM extraction
- `auto_progress()` → transitions to next stage or parks

### State Machine (`state_machine.py`)
- LangGraph state for V2 candidate chat
- Tracks `conversation_stage`, `pending_tool_calls`, `artifact_state`
- Structured transition planning via LLM

### Pulse Agent (`recruiter_agent/`)
- Conversation loop with RBAC-scoped tool permissions
- `propose_role_draft()` generates role draft from chat context using `_DraftCore` model
- `evaluate_assignment()` extracts signals from submission, always parks at `NEEDS_REVIEW`
- `override_stage()` for manual stage advancement (offer → hired)
- Artifact state tracked in UI via `ArtifactPanel` component

---

## V1 Files to Ignore

These files exist for backward compatibility during the V1→V2 migration and reference the old pipeline architecture. They delegate to V2 services internally and will be removed once all transitions are complete.

### Activities (`backend/src/activities/`)
| File | Superseded By |
|------|---------------|
| `v1_ceo_brief.py` | `pipeline_engine.py` transition planning |
| `v1_dispatch_assessment.py` | `stage_runner.py` assignment flow |
| `v1_dispatch_meeting_bot.py` | `smart_scheduler.py` meeting scheduling |
| `v1_evaluate_voice_call.py` | `evaluation.py` Gemini audio analysis |
| `v1_journey_report.py` | `agent/generators.py` candidate report |
| `v1_meeting_analysis.py` | `evaluation.py` structured analysis |
| `v1_parse_assignment.py` | `tools/registry.py` assignment parsing |
| `v1_role_drafting.py` | `recruiter_agent/tools.py` Pulse agent drafting |
| `v1_schedule_meeting.py` | `smart_scheduler.py` slot management |
| `v1_voice_screening.py` | `services/stage_runner.py` voice gate |

### Pipeline (`backend/src/pipeline/`)
| File | Superseded By |
|------|---------------|
| `v1.py` | `services/pipeline_engine.py` + `services/stage_runner.py` |

### API (`backend/src/api/`)
| File | Superseded By |
|------|---------------|
| `role_drafting.py` | `recruiter_agent/tools.py` Pulse agent artifact flow |

### Models (`backend/src/models/`)
| File | Status |
|------|--------|
| `v1.py` | V1-specific Pydantic schemas, retained for DB row compatibility |

---

## Tech Stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.12+, FastAPI, SQLAlchemy async, Pydantic v2 |
| Orchestration | Temporal (workflows) + Arq (background jobs) |
| LLM | LiteLLM (Claude 3.5 Sonnet via Anthropic, GPT-4o / GPT-4o-mini via OpenAI) |
| Tracing | Langfuse (prompt versioning + trace observability) |
| Voice | ElevenLabs ConvAI, Gemini 2.0 Flash (audio evaluation) |
| Meetings | Google Meet (Calendar API), Recall.ai / Read.ai bots |
| DB | PostgreSQL 16 + pgvector |
| Cache | Redis 7 |
| Storage | Cloudflare R2 |
| Email | Microsoft Graph / Resend / SMTP (fallback chain) |
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind, SWR |
| Infra | Docker Compose, Alembic migrations, Caddy reverse proxy |

---

## Quickstart

```bash
# 1. Environment
cp backend/.env.example backend/.env
# Fill required keys: ANTHROPIC_API_KEY, JWT_SIGNING_SECRET, DASHBOARD_KEYS

# 2. Start services
docker compose up -d

# 3. Migrations run automatically (RUN_MIGRATIONS=1)

# Access points:
#   Dashboard:  http://localhost:3100
#   API docs:   http://localhost:8000/docs
```

---

## Interview Scheduling Flow

1. Recruiter triggers a round (technical / CEO / HR / custom)
2. Panel member for that role_type receives email with scheduling link
3. Panel member picks 3 time slots within next 15 days
4. Candidate receives email with the 3 options
5. Candidate picks one (or proposes custom time)
6. Google Meet link created, calendar invites sent to both parties

Round-to-panel mapping is generic — add/remove rounds without code changes.

---

## License

Proprietary — Inspirelabs Solutions Pvt. Ltd.
