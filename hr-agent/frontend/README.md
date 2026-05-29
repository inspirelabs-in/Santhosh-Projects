# Hiring Agent Dashboard

Next.js 14 + TypeScript + Tailwind + shadcn-style UI. Wired to the backend's
`/dashboard/*` REST API.

## Run it

From `frontend/`:

```bash
npm install           # or: pnpm install / yarn
cp .env.local.example .env.local   # sets NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev           # serves on http://localhost:3100
```

Make sure the FastAPI backend is running on `:8000` (`uvicorn src.api.main:app --reload`).
The backend already has CORS open for `http://localhost:3100`.

## Sign in

When you hit `/` you'll be redirected to `/login`. Paste one of the dashboard keys
from your backend `.env`'s `DASHBOARD_KEYS`:

| Role | Example key from your `.env` |
|---|---|
| Admin | `admin_0-P3SjaqPBz_QrbhvovtukJOQuA0jO_a` |
| Recruiter | `rec_-5lLl6TdyhNz4Id59Yw99Be77z-fc8v3` |
| Viewer | `view_aW-qn_vaAp_ZeV6cVAl4BrQ3ae4CU1VT` |

The key is stored in `localStorage` and sent as `X-Dashboard-Key` on every request.

## Routes

| Route | Description |
|---|---|
| `/dashboard` | Funnel + tier split + channel delivery + override rate |
| `/inbox` | Applications flagged for HR review (low confidence, role unclear) |
| `/shortlist` | Scored candidates filtered by tier, approve/reject inline |
| `/scheduling` | Interviews: proposed / confirmed / completed |
| `/cold-pool` | Non-responders after 10-day progressive retry |
| `/candidates` | Flat list across roles |
| `/candidates/[id]` | Full profile: overview · parsed resume · applications · audit trail |
| `/roles` | List + status controls |
| `/roles/new` | Create a new role |
| `/roles/[id]` | Edit JD, constraints, screening questions, interviewer panel |
| `/audit` | Searchable append-only activity log |
| `/settings` | LLM models, DPDP retention, feature flags, integration health |

## Production build

```bash
npm run build
npm start
```

For production, run behind the same reverse proxy as the API, scope the
`NEXT_PUBLIC_API_BASE_URL` to the production origin, and tighten the backend
CORS `allow_origins` list.
