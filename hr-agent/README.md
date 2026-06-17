# HR Agent

AI-powered recruitment automation platform. Handles the full hiring pipeline from
application intake through screening, voice interviews, technical assessments,
panel scheduling, and offer management.

**HR-in-the-loop**: the agent drafts and routes; humans decide. Only clear-pass
candidates auto-advance. Everything else lands in `needs_hr_review`.

---

## Pipeline

```
applied → parse_resume → fit_score → screening → voice_screen → assignment
  → tech_interview → ceo_interview → offer → hired / rejected
```

Each stage is a Temporal activity. Candidates progress automatically on
clear-pass; otherwise queued for HR review. Circuit breaker + fallback manager
handle transient failures.

---

## Tech Stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.12+, FastAPI, SQLAlchemy async, Pydantic v2 |
| Orchestration | Temporal (workflows) + Arq (background jobs) |
| LLM | LiteLLM (Claude via Anthropic), Langfuse tracing |
| Voice | ElevenLabs ConvAI |
| Meetings | Google Meet (Calendar API), Recall.ai / Read.ai bots |
| DB | PostgreSQL 16 + pgvector |
| Cache | Redis 7 |
| Storage | Cloudflare R2 |
| Email | Microsoft Graph / Resend / SMTP (fallback chain) |
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind, SWR |
| Infra | Docker Compose, Alembic migrations, Caddy reverse proxy |

---

## Project Structure

```
backend/
  src/
    activities/       Temporal activity functions (one per pipeline stage)
    agent/            V2 LangGraph candidate chat agent
    api/              FastAPI routes (dashboard, webhooks, chat, recruiter)
    channels/         Email, SMS, Teams, WhatsApp + Jinja2 templates
    db/               SQLAlchemy models, repositories, Alembic migrations
    llm/              LLM client + versioned prompt templates
    models/           Pydantic schemas
    pipeline/         V1 orchestrator + chat invite
    recruiter_agent/  Pulse recruiter agent (runner, tools, RBAC)
    services/         Business logic (scheduling, voice, config, etc.)
    workers/          Arq background job definitions
frontend/
  src/
    app/              Next.js pages (dashboard, candidates, settings, etc.)
    components/       React components (chat, config, layout)
    lib/              API client, hooks, types
emotion-service/      Paralinguistic emotion analysis microservice
```

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

## Key Features

- **Resume parsing** — PDF extraction + LLM-powered profile structuring
- **Fit scoring** — Candidate-vs-JD scoring with configurable thresholds
- **Screening** — Auto-generated questions tailored to each resume
- **Voice screening** — ElevenLabs ConvAI phone interviews with emotion analysis
- **Assignments** — Timed technical assessments with auto-parsing
- **Interview scheduling** — Panel availability → candidate slot selection → Google Meet booking
- **Dual chat** — Candidate chat (LangGraph agent) + Recruiter chat (Pulse agent)
- **Observability** — Langfuse tracing, audit log, rate limits

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
