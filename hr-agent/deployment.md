# Deployment Strategy

## Architecture

Every component is independently deployable. No shared VM. Each has its own
provider, scaling policy, and failure domain.

```
                         Vercel (free)
                ┌──────────────────────────────┐
                │  Frontend (Next.js)          │
                │  No Docker. Vercel reads     │
                │  next.config.js directly.    │
                │  Build: npm run build        │
                │  Runtime: Node.js            │
                │  Dir: frontend/              │
                └──────────┬───────────────────┘
                           │ HTTPS (your domain or .vercel.app)
                ┌──────────▼───────────────────┐
                │  Backend API (FastAPI)       │  ← Render / Fly.io
                │  Dockerfile:                 │
                │    backend/infra/docker/     │
                │    Dockerfile                │
                │  Entrypoint:                 │
                │    infra/docker/entrypoint.sh│
                │  CMD:                        │
                │    uvicorn src.api.main:app  │
                │    --host 0.0.0.0 --port 8000│
                 │  Depends on: PG, Redis, S3    │
                 └──────────┬───────────────────┘
                            │
           ┌────────────────┼──────────────────────┐
           ▼                ▼                      ▼
    Managed PG        Managed Redis          Backblaze B2
    (Neon free)       (Upstash free)         (free 10GB)
    pgvector req'd    Required by Arq        S3-compatible
           Stores: resumes,
                                             recordings, consent
          ┌────────────────┴──────────────────────┐
          ▼                                       ▼
   Arq Worker (same image)                  External APIs (no hosting)
   Dockerfile: same as backend              ├── ElevenLabs (voice calls)
   CMD: arq src.workers.main.WorkerSettings ├── Resend / SendGrid (email)
   Runs: 15 job types + 7 cron jobs        ├── Langfuse (LLM tracing)
    Depends on: PG, Redis, S3 (same env)    ├── Google Gemini (audio eval)
                                           ├── Google Meet API (calendar)
                                           └── Microsoft Graph (email)
```

---

## Project File Map (for deployment reference)

```
hr-agent/
├── frontend/                          ← Deploy on Vercel
│   └── src/app/(app)/*                ← All pages auto-detected by Next.js
├── backend/
│   ├── pyproject.toml                 ← Python dependencies
│   ├── alembic.ini                    ← Alembic config (points to src/db/migrations)
│   ├── infra/docker/
│   │   ├── Dockerfile                 ← Builds the backend image
│   │   └── entrypoint.sh              ← Runs migrations, then exec CMD
│   ├── src/
│   │   ├── api/main.py                ← FastAPI app (uvicorn target)
│   │   ├── workers/main.py            ← Arq worker entrypoint
│   │   ├── workers/jobs.py            ← 15 job functions + 7 cron jobs
│   │   ├── activities/                ← All business logic (called by API + jobs)
│   │   ├── db/migrations/versions/    ← Alembic migration files
│   │   ├── services/                  ─┐
│   │   ├── llm/                       ─┤ All imported by api + workers at runtime
│   │   ├── models/                    ─┤
│   │   ├── channels/                  ─┘
│   │   └── config.py                  ← Settings from env vars (pydantic-settings)
│   └── .env                           ← Local dev only. NOT used in production.
└── emotion-service/                   ← SKIP. Not deployed.
```

---

## 1. PostgreSQL - Neon (free)

**Sign up:** https://console.neon.tech

### Step-by-step

1. Create a **Neon project** → pick region closest to your backend host
2. Under **Connection Details**, copy the connection strings:

   ```
   DATABASE_URL=postgresql+asyncpg://user:pass@ep-xxx.us-east-2.aws.neon.tech/hiring-agent?sslmode=require
   DATABASE_URL_SYNC=postgresql+psycopg://user:pass@ep-xxx.us-east-2.aws.neon.tech/hiring-agent?sslmode=require
   ```

   Note: `DATABASE_URL` uses `asyncpg` driver (for FastAPI).
   `DATABASE_URL_SYNC` uses the default `psycopg` driver (for Alembic migrations —
   the entrypoint.sh runs these before booting the app).

3. **Enable pgvector** — Neon has it pre-installed. No action needed.

4. Free tier limits:
   - 0.5 GB storage
   - 100 compute hours/month (auto-pauses after 5min idle)
   - 1 project, 1 branch

---

## 2. Redis - Upstash (free)

**Sign up:** https://console.upstash.com

### Step-by-step

1. Create a **Redis database** → pick region closest to your backend
2. Under **Connection Details**, copy the **REST URL** or **Connection String**:

   ```
   REDIS_URL=rediss://default:password@us1-steady-ape-12345.upstash.io:6379
   ```

3. Free tier limits:
   - 256 MB storage
   - Pay-per-request (10,000 reqs/day free)
   - TLS enabled by default (`rediss://`)

---

## 3. Backblaze B2 (free, no payment details)

S3-compatible object storage. No credit card required for the free tier.

**Sign up:** https://www.backblaze.com/cloud-storage

### Step-by-step

1. Create a **Backblaze B2** account (email + password only — no payment info)
2. Go to **Buckets** → **Create a Bucket** → name it `hiring-agent-resumes`, set to **Private**
3. Create a second bucket → name it `hiring-agent-consent`, set to **Private**
4. Go to **App Keys** → **Generate New Master Application Key** (or a limited key scoped to both buckets)
5. Save the credentials:

   ```
   R2_ACCESS_KEY_ID=<keyID>                 # Backblaze calls this keyID
   R2_SECRET_ACCESS_KEY=<applicationKey>    # Backblaze calls this applicationKey
   R2_ENDPOINT_URL=https://s3.us-west-004.backblazeb2.com
   R2_BUCKET_RESUMES=hiring-agent-resumes
   R2_BUCKET_CONSENT=hiring-agent-consent
   ```

   The endpoint URL depends on your region (`us-west-004`, `eu-central-003`, etc. — visible in the B2 dashboard).

6. Free tier: 10 GB storage, 2500 transactions/day (ample for this workload)

---

## 4. Backend API - Render Web Service (free)

**Sign up:** https://dashboard.render.com

### Step-by-step

#### 4a. Build the Docker image and push to a registry

```bash
# Build context = backend/ directory (Dockerfile reads pyproject.toml, src/, etc.)
# The Dockerfile path relative to context: infra/docker/Dockerfile
docker build -t your-registry/hiring-agent-backend:latest \
  -f backend/infra/docker/Dockerfile \
  backend/

docker push your-registry/hiring-agent-backend:latest
```

If you don't have a registry, Render can build from your git repo directly
(see 4b alternative).

**What the Dockerfile does:**
1. Starts from `python:3.12-slim`
2. Installs system deps: `tesseract-ocr`, `poppler-utils`, `libmagic1` (needed for
   resume parsing - PDF text extraction, file type detection)
3. Installs `uv` (fast pip-compatible installer), then `pip install -e ".[dev]"`
4. Copies `src/`, `tests/`, `alembic.ini`, `entrypoint.sh`
5. Entrypoint runs `alembic upgrade head` then `exec CMD`
6. Default CMD: `uvicorn src.api.main:app --host 0.0.0.0 --port 8000`

#### 4b. OR: Deploy directly from GitHub (Render auto-builds)

1. In Render dashboard → **New Web Service** → Connect your GitHub repo
2. Set:
   - **Name:** `hr-agent-backend`
   - **Root Directory:** `backend`
   - **Runtime:** `Docker`
   - **Dockerfile Path:** `infra/docker/Dockerfile`
   - **Health Check Path:** `/health`
   - **Start Command:** (leave blank — Dockerfile's CMD is used)

3. Set env vars (all required):

   ```
   DATABASE_URL=postgresql+asyncpg://...
   DATABASE_URL_SYNC=postgresql+psycopg://...
   REDIS_URL=rediss://...
   R2_ACCESS_KEY_ID=...
   R2_SECRET_ACCESS_KEY=...
   R2_ENDPOINT_URL=https://s3.<region>.backblazeb2.com
   R2_BUCKET_RESUMES=hiring-agent-resumes
   R2_BUCKET_CONSENT=hiring-agent-consent
   RUN_MIGRATIONS=1
   SECRET_KEY=<any random 32+ char string>
   ```

4. Optional env vars (enable features):

   ```
   # --- Voice calls (ElevenLabs) ---
   ELEVENLABS_API_KEY=...
   ELEVENLABS_AGENT_ID=...
   ELEVENLABS_PHONE_NUMBER_ID=...
   ELEVENLABS_WEBHOOK_SECRET=...
   ENABLE_VOICE_SCREENING=true
   ENABLE_VOICE_CALLS=true

   # --- Email (Resend) ---
   RESEND_API_KEY=...
   RESEND_FROM_EMAIL=hr@yourcompany.com

   # --- LLM tracing (Langfuse) ---
   LANGFUSE_PUBLIC_KEY=...
   LANGFUSE_SECRET_KEY=...
   LANGFUSE_HOST=https://cloud.langfuse.com

   # --- Google API (Meet scheduling + Gmail) ---
   GOOGLE_MEET_CREDENTIALS_JSON={"installed":{...}}
   GOOGLE_CALENDAR_ID=primary

   # --- Microsoft Graph (email fallback) ---
   MICROSOFT_GRAPH_CLIENT_ID=...
   MICROSOFT_GRAPH_CLIENT_SECRET=...
   MICROSOFT_GRAPH_TENANT_ID=...

   # --- Sentry (error tracking) ---
   SENTRY_DSN=...
   ```

5. **Free tier behavior:** Render spins down the service after 15 minutes of
   inactivity. First request after idle takes ~30 seconds (cold start).

   **Workaround:** Create a free cron job at https://cron-job.org that pings
   `https://hr-agent-backend.onrender.com/health` every 5 minutes.

---

## 5. Arq Worker - Render Background Worker (free)

Same Docker image as the backend API, different start command.

### Step-by-step

1. In Render dashboard → **New Background Worker** → same GitHub repo
2. Set:
   - **Name:** `hr-agent-worker`
   - **Root Directory:** `backend`
   - **Runtime:** `Docker`
   - **Dockerfile Path:** `infra/docker/Dockerfile`
   - **Start Command:** `arq src.workers.main.WorkerSettings`

3. **Copy ALL env vars from the Backend API** — identical set. The worker
   connects to the same PostgreSQL, Redis, and Backblaze B2.

4. **What the worker does (15 job types):**

   | Job function | File | What it does |
   |--------------|------|-------------|
   | `dispatch_voice_screening` | `workers/jobs.py:40` | Starts a voice screening call via ElevenLabs |
   | `dispatch_voice_call` | `workers/jobs.py:58` | General voice call dispatch (fallback) |
   | `evaluate_voice_call` | `workers/jobs.py:78` | Processes call recording, scores candidate |
   | `dispatch_assessment` | `workers/jobs.py:98` | Sends take-home assignment email |
   | `dispatch_meeting_bot` | `workers/jobs.py:116` | Invites meeting bot (Recall/Read.ai) |
   | `analyze_meeting` | `workers/jobs.py:136` | Processes meeting transcript, scores candidate |
   | `schedule_meeting` | `workers/jobs.py:158` | Books Google Meet or Teams meeting |
   | `schedule_meeting_reattempt` | `workers/jobs.py:178` | Retry scheduling on failure |
   | `generate_ceo_brief` | `workers/jobs.py:200` | Generates CEO round brief document |
   | `smart_schedule_meeting` | `workers/jobs.py:220` | Finds optimal time slots via AI |
   | `panel_availability_request` | `workers/jobs.py:240` | Emails panel members for availability |
   | `poll_due_callbacks` | `workers/jobs.py:260` | Polls ElevenLabs for completed calls |
   | `reconcile_stuck_voice_calls` | `workers/jobs.py:280` | Fixes stuck voice call states |
   | `pipeline_sla_monitor` | `workers/jobs.py:300` | Alerts on pipeline stage timeouts |
   | `prune_old_artifacts` | `workers/jobs.py:320` | Cleans up old R2 objects |
   | `assignment_deadline_reminders` | `workers/jobs.py:340` | Sends reminders for pending assignments |
   | `reconcile_stuck_meetings` | `workers/jobs.py:360` | Fixes stuck meeting states |
   | `campaign_dispatch_tick` | `workers/jobs.py:380` | Voice campaign dispatcher |

5. **7 cron schedules** (defined in `workers/main.py:104-147`):

   | Cron job | Schedule | Purpose |
   |----------|----------|---------|
   | `poll_due_callbacks` | Every 15s | Check for completed voice calls |
   | `campaign_dispatch_tick` | Every 15s | Dispatch queued voice campaigns |
   | `reconcile_stuck_voice_calls` | Every 15min | Fix stuck call states |
   | `pipeline_sla_monitor` | Every hour | Check pipeline SLAs |
   | `prune_old_artifacts` | Daily 3:11am | Clean old files from R2 |
   | `assignment_deadline_reminders` | Every 30min | Remind about due assignments |
   | `reconcile_stuck_meetings` | Every 15min | Fix stuck meetings |

6. **Free tier behavior:** Same as Web Service — cold starts after idle.
   Since this is a background worker, Render keeps it running as long as it
   has work. Idle ~15min also applies.

---

## 6. Frontend - Vercel (free)

**Sign up:** https://vercel.com

### Step-by-step

1. **Import your GitHub repo** in Vercel dashboard
2. Set:
   - **Root Directory:** `frontend`
   - **Framework:** `Next.js`
   - **Build Command:** `npm run build`
   - **Output Directory:** `.next`

3. Set env var:

   ```
   NEXT_PUBLIC_API_BASE_URL=https://hr-agent-backend.onrender.com
   ```

   (Or wherever your backend API is deployed.)

4. **Deploy.** Every push to the main branch auto-deploys.

5. **How the frontend connects to backend:**
   - `frontend/src/lib/client.ts` reads `NEXT_PUBLIC_API_BASE_URL`
   - All API calls go to `<NEXT_PUBLIC_API_BASE_URL>/<endpoint>`
   - No proxying needed — direct HTTPS from browser to Render

---

## 7. What NOT to deploy

| Service | Container in docker-compose | Why skip |
|---------|---------------------------|----------|
| **Temporal Server** | Removed from compose | Dead code. Pipeline runs on FastAPI BackgroundTasks + Arq.
| **Emotion Service** | `emotion-service/` | Voice eval falls back to text-only gpt-4o-mini when emotion service is unavailable. Emotion service is optional, not required.
| **MinIO** | `minio` + `minio-init` | Replaced by Backblaze B2 (S3-compatible, no payment details needed).
| **MailHog** | `mailhog` | Dev only. Production uses Resend / SendGrid / Microsoft Graph.
| **Postgres container** | `postgres` | Use managed Neon/Supabase — they handle backups, patching, HA.
| **Redis container** | `redis` | Use managed Upstash/Redis Cloud — serverless, no ops.

### Temporal: Why it's dead

- All `@activity.defn` decorators in `src/activities/` are dead wrappers — the
  real logic runs in plain async functions called by the API and Arq jobs
- Zero `@workflow.defn` definitions exist anywhere in the codebase
- No Temporal Worker entrypoint exists
- The Temporal Client in `src/services/temporal_client.py` is only called
  from `src/api/dashboard.py:642-654` where it silently fails (no workflows
  to signal)
- The `temporalio` dependency in `pyproject.toml:18` is unused at runtime
- `docker-compose.yml:3` explicitly says: *"Temporal + mail-poller + worker
  removed (pipeline runs on FastAPI BackgroundTasks)"*

### Emotion Service: Why it's safe to skip

- `src/services/gemini_audio_eval.py` is the primary voice call evaluator
  (uses Google Gemini with audio)
- The fallback in `src/activities/v1_evaluate_voice_call.py:262` uses
  `gpt-4o-mini` (text-only transcript evaluation)
- The emotion service was called for paralinguistic analysis only — not
  required for evaluation
- Removing it just means the emotion analysis step is skipped

---

## 8. Full Env Vars Reference

### Where settings are read from

All env vars are read in `backend/src/config.py` using `pydantic-settings`.
The class `Settings` defines every variable with its default. If an env var
is not set, the default is used.

### Required (app won't start without these)

| Env var | Config field | Example | Source |
|---------|-------------|---------|--------|
| `DATABASE_URL` | `database_url` | `postgresql+asyncpg://user:pass@ep-xxx.neon.tech/db` | Neon dashboard |
| `DATABASE_URL_SYNC` | `database_url_sync` | `postgresql+psycopg://user:pass@ep-xxx.neon.tech/db` | Same, sync driver |
| `REDIS_URL` | `redis_url` | `rediss://default:pass@us1-xxx.upstash.io:6379` | Upstash dashboard |
| `R2_ACCESS_KEY_ID` | `r2_access_key_id` | `<40-char string>` | Backblaze B2 App Key ID |
| `R2_SECRET_ACCESS_KEY` | `r2_secret_access_key` | `<40-char string>` | Backblaze B2 Application Key |
| `R2_ENDPOINT_URL` | `r2_endpoint_url` | `https://s3.us-west-004.backblazeb2.com` | Backblaze B2 S3 endpoint (region-dependent) |
| `R2_BUCKET_RESUMES` | `r2_bucket_resumes` | `hiring-agent-resumes` | B2 bucket for resume PDFs, recordings, transcripts |
| `R2_BUCKET_CONSENT` | `r2_bucket_consent` | `hiring-agent-consent` | B2 bucket for consent artifacts |
| `RUN_MIGRATIONS` | — | `1` | Set to `1` to run alembic on boot |
| `SECRET_KEY` | `secret_key` | `<random 32+ chars>` | Generate via `openssl rand -hex 32` |

### Optional (feature-specific)

| Env var | Config field | For feature |
|---------|-------------|-------------|
| `ELEVENLABS_API_KEY` | `elevenlabs_api_key` | Voice calls |
| `ELEVENLABS_AGENT_ID` | `elevenlabs_agent_id` | Voice calls |
| `ELEVENLABS_PHONE_NUMBER_ID` | `elevenlabs_phone_number_id` | Voice calls |
| `ELEVENLABS_WEBHOOK_SECRET` | `elevenlabs_webhook_secret` | Voice webhook verification |
| `ENABLE_VOICE_SCREENING` | `enable_voice_screening` | Voice pipeline toggle |
| `ENABLE_VOICE_CALLS` | `enable_voice_calls` | Voice call toggle |
| `RESEND_API_KEY` | `resend_api_key` | Transactional email |
| `RESEND_FROM_EMAIL` | `resend_from_email` | Sender address |
| `LANGFUSE_PUBLIC_KEY` | `langfuse_public_key` | LLM tracing |
| `LANGFUSE_SECRET_KEY` | `langfuse_secret_key` | LLM tracing |
| `LANGFUSE_HOST` | `langfuse_host` | LLM tracing host |
| `GOOGLE_MEET_CREDENTIALS_JSON` | `google_meet_credentials_json` | Google Meet scheduling |
| `GOOGLE_CALENDAR_ID` | `google_calendar_id` | Calendar integration |
| `MICROSOFT_GRAPH_CLIENT_ID` | `msgraph_client_id` | MS Graph email |
| `MICROSOFT_GRAPH_CLIENT_SECRET` | `msgraph_client_secret` | MS Graph email |
| `MICROSOFT_GRAPH_TENANT_ID` | `msgraph_tenant_id` | MS Graph email |
| `SENTRY_DSN` | `sentry_dsn` | Error tracking |
| `LOG_LEVEL` | `log_level` | Logging (default: INFO) |
| `APP_BASE_URL` | `app_base_url` | Public URL for email links |
| `CORS_ORIGINS` | `cors_origins` | CORS (default: wildcard) |

---

## 9. Recommended Architecture (Independent deploy)

```
Frontend  ──→ Vercel                  (free, always hot, global CDN)
PostgreSQL ──→ Neon                   (free, auto-pause when idle)
Redis     ──→ Upstash                 (free, serverless, pay-per-req)
Backend   ──→ Render Web Service      (free, cold starts after idle)
Worker    ──→ Render Background Worker(free, same)
Storage   ──→ Backblaze B2           (free 10GB)
Monitoring ──→ Sentry free tier       (5k events/month)
LLM trace  ──→ Langfuse cloud         (free 50k obs/month)
Voice      ──→ ElevenLabs dev tier    (limited minutes)
Email      ──→ Resend free tier       (100 emails/day)
```

Total cost: **$0/month**

### Why this beats a single Oracle VM

| Concern | Single VM | Independent deploy |
|---------|-----------|-------------------|
| **Failure domain** | VM goes down → everything down | Can lose Render → DB still running, Vercel still serving |
| **Scaling** | Vertical only (bigger VM) | Scale worker independently of API |
| **Upgrades** | Stop everything, update, restart | Blue-green per component |
| **Backups** | You must set up pg_dump cron | Neon handles point-in-time recovery |
| **Cold start** | None (always on) | Yes (mitigated by cron-job ping) |
| **Ops burden** | SSH, systemd, pg maintenance, redis config | No SSH. Each provider has its own dashboard |

### Mitigate Render cold starts

Create a free cron job at https://cron-job.org:
- **URL:** `https://hr-agent-backend.onrender.com/health`
- **Schedule:** Every 5 minutes
- **Method:** GET

This keeps the backend API warm. The worker cold-starts less frequently
because Render keeps it alive while jobs are running.

---

## 10. Build & Deploy Step-by-Step

### 10.1 Initial setup (one time)

```bash
# 1. Set up Neon Postgres → copy connection strings
# 2. Set up Upstash Redis → copy REDIS_URL
# 3. Set up Backblaze B2 → generate App Key, create bucket
# 4. Set up Resend → generate API key, verify domain
# 5. Set up ElevenLabs → create agent, get IDs
# 6. Set up Langfuse → get public + secret keys
```

### 10.2 Deploy backend

```bash
# Option A: Render auto-build from GitHub
#   1. New Web Service → select repo
#   2. Root dir: backend
#   3. Runtime: Docker
#   4. Dockerfile path: infra/docker/Dockerfile
#   5. Add all env vars from section 8
#   6. Deploy

# Option B: Build & push to registry
docker build -t ghcr.io/your-org/hiring-agent-backend:latest \
  -f backend/infra/docker/Dockerfile backend/
docker push ghcr.io/your-org/hiring-agent-backend:latest
# Then in Render: New Web Service → Deploy from existing image
```

### 10.3 Deploy worker

```
# Render: New Background Worker → same repo/image
# Root dir: backend
# Start command: arq src.workers.main.WorkerSettings
# Same env vars as backend API
```

### 10.4 Deploy frontend

```
# Vercel: Import repo → Root dir: frontend
# Env: NEXT_PUBLIC_API_BASE_URL = https://hr-agent-backend.onrender.com
# Deploy
```

### 10.5 Verify

```bash
# 1. Health check
curl https://hr-agent-backend.onrender.com/health
# Expected: {"status":"ok"} (200)

# 2. Dashboard API
curl https://hr-agent-backend.onrender.com/dashboard/v1/metrics?key=your_admin_key

# 3. Frontend
open https://hr-agent.vercel.app

# 4. Worker (check logs in Render dashboard)
# Expected: "arq worker starting; queue=hiring-agent"
```

---

## 11. Runbook

### Migrations
```bash
# Automatic: runs on every backend boot when RUN_MIGRATIONS=1
# Manual (if you need to run outside the container):
alembic upgrade head
```

### Check worker health
```bash
# Via Render dashboard: Logs tab for hr-agent-worker
# Expected: "arq worker starting; queue=hiring-agent" on startup
# Cron jobs fire at their scheduled intervals (see section 5)
```

### View queued jobs
```bash
redis-cli -u $REDIS_URL LLEN arq:hiring-agent:queue
```

### View failed jobs
```bash
redis-cli -u $REDIS_URL ZREVRANGE arq:hiring-agent:failed 0 10
```

### Restart a component
- **Render:** Dashboard → Service → Manual Deploy → Clear build cache & deploy
- **Vercel:** Dashboard → Frontend → Redeploy
- **Neon:** Compute auto-restarts on demand (no action needed)

### Update env vars
- **Render:** Dashboard → Service → Environment → add/change → Save → Service auto-restarts
- **Vercel:** Dashboard → Project → Settings → Environment Variables → Redeploy

### Rollback
- **Render:** Dashboard → Service → Manual Deploy → Deploy previous image
- **Vercel:** Dashboard → Deployments → → ⋮ → Promote to Production

---

## 12. Staging / Preview Environment

| Component | Strategy |
|-----------|----------|
| **Frontend** | Vercel Preview Deployments — auto-created per PR |
| **Backend** | Second Render Web Service with `DATABASE_URL` pointing to Neon branch |
| **Database** | Neon branching — instant copy of production DB for each PR |
| **Worker** | Not needed for staging (use BackgroundTasks directly for testing) |

---

## 13. Security Checklist

- [ ] `SECRET_KEY` is a random 32+ char string (not the default from config.py)
- [ ] `DATABASE_URL` uses `sslmode=require` (Neon enforces this by default)
- [ ] `REDIS_URL` uses `rediss://` (TLS) — Upstash enforces this
- [ ] CORS origins are set (if frontend is on a custom domain)
- [ ] Admin API keys are set in the dashboard (3 keys generated on first boot)
- [ ] `RUN_MIGRATIONS` is `1` so migrations run on deploy
- [ ] ElevenLabs webhook secret is set (verifies callback authenticity)

---

## 14. Cost Breakdown (all free tier)

| Provider | Service | Free tier limit | Est. monthly cost |
|----------|---------|----------------|-------------------|
| Vercel | Frontend hosting | 100GB bandwidth, 6000 build mins | $0 |
| Neon | PostgreSQL | 0.5GB, 100 compute hours | $0 |
| Upstash | Redis | 256MB, 10k reqs/day | $0 |
| Render | Backend API + Worker | 512MB RAM each, 750hrs/month | $0 |
| Backblaze B2 | Object storage | 10GB, 2500 tx/day | $0 |
| Resend | Transactional email | 100 emails/day | $0 |
| ElevenLabs | Voice calls | 10 min/month (dev tier) | $0 |
| Langfuse | LLM tracing | 50k observations/month | $0 |
| Cron-job.org | Uptime pings | 60 jobs, 1min intervals | $0 |
| **Total** | | | **$0** |
