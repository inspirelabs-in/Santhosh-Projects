# Hiring Agent — V1

AI-powered recruitment automation for GrabOn (Inspirelabs Solutions Pvt. Ltd.).
**V1 scope**: HR creates a Role with an assignment brief; the agent generates
screening questions tailored to each applicant's resume, emails them,
evaluates responses, auto-advances clear-pass candidates to the assignment
stage, parses the submitted assignment, and produces a journey report for HR.

**HR-in-the-loop**: the agent drafts and routes; humans decide. Only
`clear_pass` responses auto-advance. Everything else lands in
`needs_hr_review`. No auto-reject.

**DPDP Act 2023** compliant: explicit consent, audit log, right-to-erasure.

---

## V1 pipeline (state machine)

```
applied
  └─ parse resume -> generate screening questions -> email
screening_sent
  └─ candidate submits via /apply/[token]
screening_submitted
  └─ LLM evaluate (gpt-4o)
screening_evaluated
  ├─ clear_pass        -> assignment_sent (agent emails brief)
  └─ everything else   -> needs_hr_review (HR decides)
assignment_sent
  └─ candidate uploads files + links + notes
assignment_submitted
  └─ LLM parse + generate journey report (gpt-4o)
report_ready
  └─ HR hires or rejects
```

---

## Stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy async |
| Orchestration | FastAPI `BackgroundTasks` + DB state machine (no Temporal) |
| LLM | LiteLLM → OpenAI (`gpt-4o-mini` fast, `gpt-4o` smart) |
| Tracing | Langfuse (optional, recommended) |
| DB | Postgres 16 + pgvector |
| Cache/RL | Redis 7 |
| Storage | Cloudflare R2 (prod) / MinIO (dev) |
| Email | Resend (prod) / SMTP MailHog (dev) |
| Frontend | Next.js 14 App Router, Tailwind, Radix UI, Fraunces + Inter + JetBrains Mono |

---

## Repo layout

```
HR Agent/
├── backend/
│   ├── src/
│   │   ├── api/                # webhooks.py, apply.py, roles.py, v1_dashboard.py, dashboard.py
│   │   ├── activities/         # intake, parse_resume, v1_generate_screening, v1_evaluate_screening,
│   │   │                       #   v1_send_assignment, v1_parse_assignment, v1_journey_report
│   │   ├── pipeline/v1.py      # orchestrator (runs under BackgroundTasks)
│   │   ├── channels/           # email + templates
│   │   ├── llm/                # LiteLLM client + versioned prompts
│   │   ├── models/             # pydantic shapes (candidate.py, v1.py)
│   │   ├── db/                 # SQLAlchemy ORM + alembic migrations + repositories
│   │   ├── services/           # consent, dedup, file_storage, rate_limit, screening_url
│   │   └── config.py
│   ├── .env.example
│   └── alembic.ini
├── frontend/                    # Next.js 14 dashboard + /apply/[token]
│   └── src/app/
│       ├── (app)/dashboard       # funnel + needs_hr_review queue
│       ├── (app)/candidates      # list + filters
│       ├── (app)/candidates/[id] # journey timeline + report + HR actions
│       ├── (app)/roles           # CRUD with assignment fields
│       └── apply/[token]         # candidate-facing screening / assignment form
├── docker-compose.yml
└── README.md
```

---

## Quickstart (dev)

```bash
# 1. env
cp backend/.env.example backend/.env
# fill: OPENAI_API_KEY, JWT_SIGNING_SECRET, DASHBOARD_KEYS

# 2. stack
docker compose up -d
docker compose --profile dev up -d mailhog    # optional, catches outbound email

# 3. alembic runs automatically in backend container (RUN_MIGRATIONS=1)

# open
# Dashboard: http://localhost:3100
# API docs:  http://localhost:8000/docs
# MinIO:     http://localhost:9001 (minioadmin / minioadmin)
# MailHog:   http://localhost:8025
```

---

## End-to-end demo

1. POST /webhooks/careers-form with name/email/role_id/consent=true + resume file.
2. Agent parses resume, generates 5-7 screening questions, emails candidate a signed `/apply/[token]` link.
3. Candidate submits → agent evaluates (gpt-4o verdict: clear_pass / needs_hr_review / clear_reject).
4. `clear_pass` → agent emails assignment brief + fresh signed link. Else → queued in `needs_hr_review`.
5. Candidate uploads assignment → agent parses (gpt-4o-mini), generates journey report (gpt-4o).
6. HR reviews in dashboard, marks `hired` or `rejected`.

---

## Observability

- Langfuse: LLM cost + trace per stage (`screening_gen`, `screening_eval`,
  `assignment_parse`, `journey_report`).
- `audit_log` table: append-only, one row per stage transition + LLM call.
- Rate limits via Redis: per-minute, per-day, per-candidate, daily USD cap.

---

## Inbound mail (V1)

Applications arriving by email are polled via IMAP (stdlib `imaplib` wrapped
in `asyncio.to_thread`), so no pub/sub infra is needed.

- **Dev**: personal Gmail with a 16-char app password (see `MAIL_INBOXES` in `.env`)
- **Prod**: `careers@grabon.in` on Outlook (`outlook.office365.com:993`)
- Poller runs inside the `backend` container (FastAPI lifespan task) — no
  separate worker process. Polls each mailbox every
  `MAIL_POLL_INTERVAL_SECONDS`, marks messages `\Seen`, dedupes via
  `processed_messages` table.
- Each new mail → intake (upload attachments to R2) → V1 pipeline.
- Role is matched by fuzzy title-in-subject/body; if no match, application
  lands in `needs_hr_review`.

## What's NOT in V1 (intentionally)

- Interview scheduling (Google Calendar)
- Cold-pool re-engagement
- WhatsApp / SMS notifications
- Temporal workflows (replaced with BackgroundTasks)
- Gmail pub/sub API (IMAP polling used instead)
- Slack / Teams integrations
