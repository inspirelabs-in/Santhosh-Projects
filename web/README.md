# grabon-intel web

Next.js 16 App Router chat workspace. Three-pane layout:

- **Left rail** — pipeline (Hot / Warm / Watchlist / Approved / Sent / Replied / Booked / Won / Lost)
- **Center** — chat workspace, dossier-in-chat, brand profile tabs
- **Right rail** — agent reasoning panel (SSE-driven), cost meter

Streaming responses, citations on every claim, dark mode default.

## Setup

```powershell
cd "D:/Lead gen/grabon_intel/web"
pnpm install
cp .env.example .env.local  # set NEXT_PUBLIC_API_BASE + API key
pnpm dev
```

API base defaults to `http://localhost:8000`. API key passed via header
`X-API-Key` from the server actions / route handlers — never exposed to
the browser bundle. (For dev convenience we also accept `NEXT_PUBLIC_API_KEY`
when `NODE_ENV !== production`.)

## Files

```
web/
  package.json
  tsconfig.json
  next.config.mjs
  postcss.config.mjs
  tailwind.config.ts
  .env.example
  src/
    app/
      layout.tsx
      page.tsx                       # workspace landing
      brands/[id]/page.tsx           # brand profile
      api/                           # route handlers proxying to backend
    components/
      pipeline-rail.tsx
      chat-workspace.tsx
      reasoning-panel.tsx
      dossier-view.tsx
      score-badge.tsx
    lib/
      api.ts                         # typed fetch
      sse.ts                         # EventSource hook
      types.ts
    styles/
      globals.css
```

This package is **not yet installed** — only the source files are committed.
Run `pnpm install` once Node 20+ is available and you're ready to wire the
UI to a live API. The backend already exposes all endpoints the components
read from.
