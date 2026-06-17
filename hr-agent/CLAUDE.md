# HR Agent — Project Instructions

## Architecture Quick Reference

### Core Entities
- `Application` — central ORM model, everything FK's to it
- `Candidate`, `Role`, `CandidateProfileRow`
- `get_settings()`, `PipelineStage`, `set_stage()`

### Key Modules
| Area | What |
|------|------|
| Resume Parse & Analytics | Parse resume activity, analytics API |
| Meeting Scheduling | Schedule + dispatch meetings (Teams/GMeet) |
| Chat Agent LangGraph | V2 candidate chat (LangGraph state machine) |
| Fit Score & Tiering | Candidate-vs-JD scoring |
| Recruiter Agent RBAC | Pulse recruiter agent permissions |
| Recruiter Chat API | Pulse API endpoints |
| Background Workers | Mail poller, stall detector, nudge, watchdog |
| Intake Pipeline | Careers form → intake activity |
| Circuit Breaker & Voice | ElevenLabs voice + resilience |

### Tech Stack
- **Backend**: Python, FastAPI, Temporal (workflows), Arq (background jobs), PostgreSQL, Redis
- **Frontend**: Next.js 14 (App Router), TypeScript, Tailwind, SWR
- **LLM**: LiteLLM (Claude via Anthropic), Langfuse tracing
- **Voice**: ElevenLabs ConvAI
- **Meetings**: Recall.ai / Read.ai bot providers, Teams/GMeet
- **Storage**: Cloudflare R2
- **Email**: Microsoft Graph / Resend / SMTP (fallback chain)
- **Infra**: Docker Compose, Alembic migrations

### Project Structure
```
backend/
  src/
    activities/     — Temporal activity functions (pipeline stages)
    agent/          — V2 LangGraph chat agent
    api/            — FastAPI routes (dashboard, webhooks, chat, recruiter)
    channels/       — Email, SMS, Teams, WhatsApp
    db/             — SQLAlchemy models, repos, Alembic migrations
    llm/            — LLM client + prompt templates
    models/         — Pydantic schemas
    pipeline/       — V1 orchestrator + chat invite
    recruiter_agent/ — Pulse recruiter agent (runner, tools, RBAC)
    services/       — Business logic (scheduling, voice, config, etc.)
    workers/        — Arq job definitions
frontend/
  src/
    app/            — Next.js pages (dashboard, candidates, CEO, HR, settings)
    components/     — React components (chat, recruiter-chat, layout, config)
    lib/            — API client, hooks, types
emotion-service/    — Paralinguistic emotion analysis microservice
```

### Pipeline Stages (PipelineStage enum)
intake → parse → fit_score → screening → voice_screen → assignment → tech_interview → ceo_interview → offer → hired/rejected

### Key Patterns
- **session_scope()** — DB session context manager, used everywhere
- **log_audit()** — Audit trail for every pipeline action
- **auto_progress** — Automatic stage advancement with circuit breaker + fallback
- **Config store** — Runtime-editable settings (env defaults + DB overlay)
- **Dual chat** — Candidate chat (LangGraph agent) + Recruiter chat (Pulse agent)
