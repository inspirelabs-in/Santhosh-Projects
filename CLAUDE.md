# GrabOn GEO Agent

GEO (Generative Engine Optimization) rank tracking system for monitoring brand visibility across AI engines.

## Tech Stack
- **Backend**: FastAPI + PostgreSQL (psycopg async)
- **Frontend**: Jinja2 templates + Tailwind CSS + Chart.js
- **Scraping**: Playwright + Camoufox browser automation
- **LLM Parsing**: OpenAI / Groq / Gemini API for response parsing
- **Deployment**: Docker Compose

## Key Architecture
- `app/agent/scraper.py` — engine scrapers (ChatGPT, Claude, Gemini, Perplexity, Google AIO, Google AI Mode)
- `app/agent/pipeline.py` — orchestrates scraping pipeline with cooldowns and scheduling
- `app/agent/parser.py` — LLM-based response parsing (brand mentions, sentiment, citations)
- `app/agent/smart_extract.py` — DOM-based response extraction from browser pages
- `app/routes/` — FastAPI route handlers (auth, api, dashboard, logs)
- `app/database.py` — async DB pool, `run_db()` is the central DB access function
- `app/config.py` — `get_settings()` provides all configuration via pydantic BaseSettings

## graphify Knowledge Graph

A persistent knowledge graph exists at `graphify-out/graph.json` (313 nodes, 506 edges, 17 communities).

### How to use the graph instead of re-reading the codebase

Before reading files to understand architecture or find connections, query the graph first:

```python
# Quick lookup — find what connects to a concept
python -c "
from graphify.build import build_from_json
import json, sys
from pathlib import Path
G = build_from_json(json.loads(Path('graphify-out/graph.json').read_text()))
node = sys.argv[1]
if node in G:
    for n in G.neighbors(node):
        e = G.edges[node, n]
        print(f'  {n} [{e.get(\"relation\",\"?\")}] conf={e.get(\"confidence_score\",\"?\")}')
else:
    matches = [n for n in G.nodes if node.lower() in n.lower()]
    for m in matches[:10]:
        print(f'  {m}: {G.nodes[m].get(\"label\",m)}')
"
```

```python
# Community overview — understand a subsystem
python -c "
import json
from pathlib import Path
analysis = json.loads(Path('.graphify_analysis.json').read_text())
labels = json.loads(Path('.graphify_labels.json').read_text())
for cid, name in labels.items():
    nodes = analysis['communities'].get(cid, [])
    print(f'{name}: {len(nodes)} nodes')
"
```

### Auto-rebuild
Git hooks (post-commit, post-checkout) are installed. The graph auto-rebuilds when code changes are committed. No manual intervention needed.

### God Nodes (most connected)
1. `run_db()` — 41 edges (central DB access)
2. `get_settings()` — 13 edges (config hub)
3. `Web Service (FastAPI App)` — 13 edges
4. `_refresh_cookies()` — 12 edges (auth)
5. `ScrapeResult` — 11 edges (data model)

### Community Map
| Community | Description |
|-----------|-------------|
| Auth & Cookie Management | Browser auth, cookie refresh, storage state |
| API Routes & Analytics | REST endpoints, analytics queries |
| Engine Scraper & Proxy | Per-engine scrapers, proxy rotation |
| UI Templates & Features | Jinja2 templates, frontend features |
| Pipeline & Scheduling | Scrape orchestration, APScheduler |
| Infrastructure & SDKs | FastAPI, Playwright, DB drivers |
| Smart DOM Extraction | Browser DOM parsing, text extraction |
| Data Models & Types | Pydantic models, enums |
| Keyword Import Pipeline | Excel import, keyword classification |
| LLM Response Parser | Multi-provider LLM parsing |
| Database & App Lifecycle | Connection pool, schema init |
| Dashboard & Charts | Dashboard route, chart data builders |
