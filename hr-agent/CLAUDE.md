# HR Agent — Project Instructions

## Knowledge Graph (Auto-Managed — DO NOT SKIP)

A knowledge graph at `graphify-out/graph.json` maps every module, function, and relationship in this codebase.
**It updates automatically** — hooks mark it stale after edits, and it rebuilds at session start.

### MANDATORY: Query graph BEFORE reading files

For ANY codebase question (where is X, how does Y work, what calls Z), ALWAYS run this first:
```python
python -c "
import json; from networkx.readwrite import json_graph; import networkx as nx; from pathlib import Path
G = json_graph.node_link_graph(json.loads(Path('graphify-out/graph.json').read_text()), edges='links')
[print(n, d.get('label',''), d.get('source_file','')) for n,d in G.nodes(data=True) if 'KEYWORD' in d.get('label','').lower()]
"
```
Replace KEYWORD with what you're looking for. This tells you exactly which files to read — no wasted token on irrelevant files.

For deeper exploration: `/graphify query "<question>"`

### Auto-update system (already configured)
- **PostToolUse hook** (Edit/Write): flags `graphify-out/.needs_update`
- **SessionStart hook**: if flag exists, runs AST-only rebuild (fast, no LLM cost)
- For non-code changes (docs/prompts): run `/graphify D:\HR Agent --update` manually (needs LLM)

### If graph seems stale
```
/graphify D:\HR Agent --update
```

## Architecture Quick Reference

### God Nodes (most connected)
- `Application` (55 edges) — central ORM, everything FK's to it
- `Candidate` (50), `Role` (49), `CandidateProfileRow` (36)
- `get_settings()` (33), `PipelineStage` (33), `set_stage()` (29)

### Key Communities
| ID | Name | What |
|----|------|------|
| 0 | Resume Parse & Analytics | Parse resume activity, analytics API |
| 1 | Meeting Scheduling | Schedule + dispatch meetings (Teams/GMeet) |
| 4 | Chat Agent LangGraph | V2 candidate chat (LangGraph state machine) |
| 5 | Fit Score & Tiering | Candidate-vs-JD scoring |
| 6 | Recruiter Agent RBAC | Pulse recruiter agent permissions |
| 7 | Recruiter Chat API | Pulse API endpoints |
| 8 | Background Workers | Mail poller, stall detector, nudge, watchdog |
| 10 | Intake Pipeline | Careers form → intake activity |
| 13 | Circuit Breaker & Voice | ElevenLabs voice + resilience |
| 17 | V2 Chat Agent Core | agent runner/graph/generators/state |

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
