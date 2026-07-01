# Deployment Strategy

## Architecture Principle

Every component is independently deployable. No shared VM. Each has its own
resource envelope, scaling policy, and failure domain.

```
                         Vercel (free)
                ┌──────────────────────────┐
                │  Frontend (Next.js)      │
                │  Build: npm run build    │
                │  No Docker               │
                └──────────┬───────────────┘
                           │ HTTPS
                ┌──────────▼───────────────┐
                │  Backend API (FastAPI)   │  ← Render / Fly.io / Oracle VM
                │  uvicorn src.api.main:app│
                │  Needs: PG, Redis, R2    │
                └──────────┬───────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
   Managed PG        Managed Redis      Cloudflare R2
   (Neon/Supabase)   (Upstash/          (object store for
    pgvector req'd    Redis Cloud)       resumes, recordings,
                                         consent docs)

          ┌────────────────┴────────────────┐
          ▼                                 ▼
   Arq Worker (same image, diff CMD)   External APIs (no hosting)
   arq src.workers.main.WorkerSettings  ├── ElevenLabs (voice)
   Needs: PG, Redis, R2                ├── Resend / SendGrid (email)
                                        └── Langfuse (tracing)
```

---

## 1. Deployable Units (5 total)

### 1.1 Frontend — Vercel (free)

| Property | Value |
|----------|-------|
| Platform | Vercel (free tier) |
| Build | `npm run build` |
| Runtime | Node.js, no Docker |
| Env | `NEXT_PUBLIC_API_BASE_URL` = backend public URL |

Deploy: Connect git repo → Vercel auto-deploys on push.

---

### 1.2 Backend API — needs a host

| Property | Value |
|----------|-------|
| Platform | Pick one: Render Web Service / Fly.io / Oracle VM |
| Dockerfile | `backend/infra/docker/Dockerfile` |
| Start cmd | `uvicorn src.api.main:app --host 0.0.0.0 --port 8000` |
| Runs migrations | Yes (via entrypoint when `RUN_MIGRATIONS=1`) |

#### What it depends on

| Dependency | Why | Free option |
|------------|-----|-------------|
| **PostgreSQL** (with pgvector) | All app data, pipeline state, audit log | **Neon** (0.5GB, 100hrs/mo compute) or **Supabase** (500MB) |
| **Redis** | Session cache, rate limiter (minor) | **Upstash** (256MB, pay-per-req) |
| **Cloudflare R2** | Resume PDFs, recordings, consent docs | Free 10GB storage, 1M ops/mo |
| **External APIs** | ElevenLabs / Resend / Langfuse | Already external, no hosting needed |

#### Env vars required

```
DATABASE_URL=postgresql+asyncpg://...
DATABASE_URL_SYNC=postgresql://...       (same DB, sync driver for Alembic)
REDIS_URL=rediss://...                    (from Upstash / Redis Cloud)
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
R2_BUCKET_NAME=hiring-agent-resumes
RUN_MIGRATIONS=1
SECRET_KEY=...
```

#### Optional env vars

```
LANGFUSE_PUBLIC_KEY=...        (tracing)
LANGFUSE_SECRET_KEY=...
ELEVENLABS_API_KEY=...         (voice screening)
ELEVENLABS_AGENT_ID=...
ELEVENLABS_PHONE_NUMBER_ID=...
ELEVENLABS_WEBHOOK_SECRET=...
RESEND_API_KEY=...             (email)
RESEND_FROM_EMAIL=...
```

---

### 1.3 Arq Worker — background job processor

| Property | Value |
|----------|-------|
| Platform | Same provider as backend (or different) |
| Image | **Same Docker image** as backend API |
| Start cmd | `arq src.workers.main.WorkerSettings` |
| Replicas | 1 (free tier), scale independently if needed |

#### What it does

- Voice screening dispatch & evaluation
- Assignment email sending
- Stall detection (stuck pipelines)
- Nudge timers (reminder emails)
- Webhook watchdog

#### What it needs

**Exactly the same env vars as Backend API** — it connects to the same
PostgreSQL, Redis, and R2. No separate infra.

#### Why Arq, not Temporal

Temporal is **deprecated** in this codebase. The docker-compose explicitly
removed it. All `@activity.defn` wrappers are dead code — the real pipeline
runs on **FastAPI BackgroundTasks + Arq** (Redis-backed job queue).

Temporal would require:
- A Temporal Server (another service to host)
- Workflow definitions (zero exist today — dead code only has activities)
- A Temporal Worker entrypoint (doesn't exist)

Arq needs only Redis — already required by the app.

---

### 1.4 PostgreSQL — managed, not self-hosted

| Provider | Free tier | pgvector | Notes |
|----------|-----------|----------|-------|
| **Neon** | 0.5GB, 100hrs/mo compute | Yes | Best — auto-pauses, branch for staging |
| **Supabase** | 500MB, paused after 1wk | Yes | Ample, but resets on pause |
| **Aiven** | 1GB, always-on | Yes | Slow free tier, no auto-pause |

**Do not run Postgres on the same VM as the app.** Managed providers handle
backups, patching, replication. The connection string is just an env var swap.

---

### 1.5 Redis — managed, not self-hosted

| Provider | Free tier | Notes |
|----------|-----------|-------|
| **Upstash** | 256MB, pay-per-request | Best — serverless, no persistent connection |
| **Redis Cloud** | 30MB | Tight but works for Arq queues |

Redis is **required** — Arq uses it for the job queue. Without Redis, the
worker cannot run.

---

## 2. What NOT to deploy

| Service | Reason |
|---------|--------|
| **Temporal Server** | Dead code. Pipeline runs on Arq + BackgroundTasks |
| **Emotion Service** | Voice eval falls back to text-only (gpt-4o-mini) when unavailable |
| **MinIO** | Replaced by Cloudflare R2 |
| **MailHog** | Replaced by Resend / SendGrid |
| **Postgres on the app VM** | Use managed Neon/Supabase |
| **Redis on the app VM** | Use managed Upstash |

---

## 3. Free-tier hosting comparison for Backend + Worker

| Provider | Free specs | Downside |
|----------|------------|----------|
| **Render** | 512MB RAM, 1CPU | Spins down after 15min idle → 30s cold start |
| **Fly.io** | 3 shared VMs, 256MB each | 30s cold start after idle |
| **Railway** | $5 credit, no free | Runs out fast |
| **Oracle Cloud AMD** | 1 CPU, 1GB RAM, always-on | No cold start, but single VM = single failure point |
| **Oracle Cloud ARM** | 4 CPUs, 24GB RAM, always-on | Best specs but single VM — defeats independence |

### Recommendation for "independent" deploy

```
Frontend  → Vercel                    (free, always hot)
PG        → Neon                      (free, auto-pause)
Redis     → Upstash                   (free, serverless)
Backend   → Render Web Service        (free, cold starts but independent)
Worker    → Render Background Worker  (free, same)
R2        → Cloudflare                (free)
```

To eliminate cold starts, add a cron-job.org ping every 5min to
`https://your-app.onrender.com/health`.

---

## 4. Staging / Preview

| Component | Strategy |
|-----------|----------|
| Frontend | Vercel preview deployments (one per branch) |
| Backend | Second Render service with `DATABASE_URL` pointing to Neon branch |
| Database | Neon branching — instant copy for each PR |
| Migrations | Run `alembic upgrade head` in backend startup (controlled via `RUN_MIGRATIONS` env var) |

---

## 5. Build & Deploy Steps

```bash
# 1. Backend + Worker image (same Dockerfile, different CMD)
docker build -t registry/backend:latest -f backend/infra/docker/Dockerfile backend/
docker push registry/backend:latest

# 2. Deploy backend API on Render
#    Image: registry/backend:latest
#    Cmd:   uvicorn src.api.main:app --host 0.0.0.0 --port 8000
#    Env:   DATABASE_URL, REDIS_URL, R2_*, RUN_MIGRATIONS=1

# 3. Deploy worker on Render
#    Image: registry/backend:latest
#    Cmd:   arq src.workers.main.WorkerSettings
#    Env:   same as backend API

# 4. Create managed Postgres on Neon + get connection string
# 5. Create managed Redis on Upstash + get connection string
# 6. Set up Cloudflare R2 bucket + generate access keys
# 7. Deploy frontend on Vercel (connect repo, set NEXT_PUBLIC_API_BASE_URL)
```

---

## 6. Runbook

### Migrations
Backend runs `alembic upgrade head` on boot when `RUN_MIGRATIONS=1`.
To run manually:
```bash
alembic upgrade head
```

### Check worker is processing jobs
```bash
# Via Redis
redis-cli -u $REDIS_URL LLEN arq:queue
```

### View job failures
```bash
redis-cli -u $REDIS_URL ZREVRANGE arq:failed 0 10
```

### Restart a specific component (Render dashboard)
No SSH needed. Each service restarts independently via the Render UI.

---

## 7. Cost Summary (all free tier)

| Service | Cost |
|---------|------|
| Vercel | $0 |
| Neon Postgres | $0 |
| Upstash Redis | $0 |
| Render (backend + worker) | $0 (cold start penalty) |
| Cloudflare R2 | $0 |
| ElevenLabs | $0 (dev tier, limited minutes) |
| Resend | $0 (100 emails/day) |
| Langfuse | $0 (cloud, 50k observations/mo) |
| **Total** | **$0** |
