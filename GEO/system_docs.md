# GrabOn GEO Agent - System Architecture Documentation

> **Generative Engine Optimization (GEO)** rank tracking system for monitoring brand visibility across AI engines.
> Built with FastAPI + PostgreSQL + Playwright/Camoufox + LLM Parsing.
> Includes SERP tracking, AI citation analysis, diagnosis engine, and verification loop.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Data Collection Architecture](#data-collection-architecture)
3. [Smart DOM Extraction](#smart-dom-extraction)
4. [LLM Response Parsing](#llm-response-parsing)
5. [Pipeline Orchestration](#pipeline-orchestration)
6. [SERP Crawler](#serp-crawler)
7. [Citation & Content Analysis](#citation--content-analysis)
8. [Diagnosis Engine](#diagnosis-engine)
9. [Verification Loop](#verification-loop)
10. [Database Schema](#database-schema)
11. [Performance Optimizations](#performance-optimizations)
12. [Real-Time Event System](#real-time-event-system)
13. [API Surface](#api-surface)
14. [Authentication & Cookie Management](#authentication--cookie-management)
15. [Resilience & Error Handling](#resilience--error-handling)
16. [Configuration Reference](#configuration-reference)
17. [Deployment](#deployment)

---

## System Overview

GEO Agent monitors how AI search engines (ChatGPT, Claude, Gemini, Perplexity, Google AIO, Google AI Mode) respond to brand-related queries. It scrapes responses, parses them with LLMs to extract brand mentions and rankings, crawls Google SERPs for organic ranking data, cross-references AI citations with SERP results, diagnoses why competitors outrank the target brand, and verifies whether applied fixes move the needle. All engine scraping uses browser automation exclusively (no API-key-based methods).

### High-Level Architecture

```
+------------------+     +-------------------+     +------------------+
|   SCHEDULER      |     |   PIPELINE        |     |   SCRAPER        |
|                  |     |                   |     |                  |
| APScheduler      +---->| Oldest-first      +---->| 6 Engine drivers |
| Continuous loop  |     | batches           |     | Camoufox browser |
| Cookie refresh   |     | Retry + cooldown  |     | Proxy rotation   |
| SERP loop        |     | Parallel engines  |     +------------------+
| Diagnosis loop   |     | Concurrency ctrl  |
| Verification chk |     +--------+----------+
+------------------+              |
                                  v
                    +-------------+-------------+
                    |                           |
           +--------+----------+    +-----------+---------+
           |   LLM PARSER      |    |   SERP CRAWLER      |
           |                   |    |                     |
           | OpenAI / Groq /   |    | Google organic      |
           | Gemini fallback   |    | curl + browser      |
           | Brand extraction  |    | BeautifulSoup parse |
           | Sentiment + rank  |    | Feature detection   |
           +--------+----------+    +-----------+---------+
                    |                           |
                    v                           v
           +--------+---------------------------+---------+
           |              POSTGRESQL                      |
           |                                              |
           | prompts, execution_logs, brand_mentions      |
           | serp_results, serp_organic_entries            |
           | citation_pages, citation_serp_overlaps       |
           | diagnoses, applied_fixes, api_costs          |
           +---------------------+------------------------+
                                 |
           +---------------------+------------------------+
           |                                              |
  +--------+----------+  +-----------+  +-----------+---------+
  |   DIAGNOSIS       |  | CONTENT   |  | VERIFICATION        |
  |   ENGINE          |  | COMPARATOR|  | LOOP                |
  |                   |  |           |  |                     |
  | Evidence gather   |  | LLM-based |  | Before/after metrics|
  | Overlap refresh   |  | page diff |  | Verdict: improved/  |
  | LLM root cause    |  | Gap finder|  |   degraded/no change|
  | Action items      |  |           |  | Backlinks to diag   |
  +-------------------+  +-----------+  +---------------------+
                                 |
                                 v
                    +------------+----------+
                    |   FASTAPI             |
                    |                       |
                    | REST API (50+         |
                    |   endpoints)          |
                    | SSE streaming         |
                    | Server-side TTL cache |
                    | Jinja2 dashboard      |
                    | (8 pages)             |
                    +-----------------------+
```

### Request Lifecycle

```
Keyword ("ajio coupon code")
    |
    v
[Pipeline] picks keyword from oldest-first queue
    |
    +---> [Scraper: ChatGPT]    --> raw text --> [Parser] --> brand_mentions
    +---> [Scraper: Gemini]     --> raw text --> [Parser] --> brand_mentions
    +---> [Scraper: Google AIO] --> raw text --> [Parser] --> brand_mentions
    +---> [Scraper: Perplexity] --> raw text --> [Parser] --> brand_mentions
    +---> [Scraper: Claude]     --> raw text --> [Parser] --> brand_mentions
    +---> [Scraper: AI Mode]    --> raw text --> [Parser] --> brand_mentions
    |
    v
[DB Commit] execution_logs + brand_mentions + coupons
    |
    v
[SERP Crawler] Google organic rankings for same keyword
    |
    v
[Citation Overlap] Cross-reference AI-cited URLs with SERP rankings
    |
    v
[Diagnosis Engine] Why does AI prefer competitors? What to fix?
    |
    v
[Verification Loop] Did the fix work? Before/after comparison.
    |
    v
[SSE Broadcast] --> connected dashboards update live
```

---

## Data Collection Architecture

### Engine Scrapers

Each AI engine has a dedicated scraper function in `app/agent/scraper.py`. All scrapers return a `ScrapeResult` containing `raw_response_text`, `raw_html_payload`, `cited_urls`, and `error_log`.

#### Engine Registry

| Engine | Type | URL Pattern | Auth Required | Response Wait |
|--------|------|-------------|---------------|---------------|
| Google AIO | Browser | `google.com/search?q={query}` | No | 25s poll |
| Google AI Mode | Browser | `google.com/search?q={query}&udm=50` | No | 40s poll |
| Perplexity | Browser | `perplexity.ai` (chat) | Yes | 45s max |
| ChatGPT | Browser | `chatgpt.com` (chat) | Yes | 50s max |
| Gemini | Browser | `gemini.google.com/app` | Yes | 50s max |
| Claude | Browser | `claude.ai/new` (chat) | Yes | 60s max |

#### Google AIO Scraper - Deep Dive

Google AIO is the most complex scraper with 4 fallback extraction strategies:

```
Navigate to google.com/search?q=...
    |
    v
Wait for DOM load + 3s
    |
    v
[AIO Detection Loop - 25 ticks]
    |-- Look for "AI Overview" text
    |-- Look for div[data-sgrd] selector
    |-- If found: break
    |-- If 25s elapsed: "No AI Overview"
    |
    v
[Expand] Click "Show more" if present
    |
    v
[Stability Wait - 15 ticks]
    |-- Monitor body text length
    |-- Require 2 consecutive stable readings
    |
    v
[Extraction - 4 strategies in order]
    |
    +-- Strategy 1: Find "AI Overview" heading
    |   -> Walk up to parent container
    |   -> Extract innerText
    |
    +-- Strategy 2: Query div[data-sgrd]
    |   -> Collect all matching elements
    |
    +-- Strategy 3: CSS selectors
    |   -> div[data-async-type="editableDirectAnswer"]
    |   -> Other known selectors
    |
    +-- Strategy 4: Full-page text fallback
        -> Find "AI Overview" index in body text
        -> Extract next 5000 characters
```

#### Chat-Based Scrapers (ChatGPT, Claude, Gemini, Perplexity)

All chat-based scrapers follow a common pattern:

```
Load storage state (cookies + localStorage)
    |
    v
Launch Camoufox browser
    |-- headless: "virtual" (no visible window)
    |-- humanize: true (human-like mouse/timing)
    |-- block_webrtc: true (prevent IP leak)
    |-- os: "windows" (fingerprint)
    |
    v
Inject auth cookies + localStorage
    |
    v
Navigate to chat URL
    |
    v
[Auth Check] - detect redirect to login page
    |-- If redirected: return auth_expired error
    |
    v
Find text input element
    |-- Try engine-specific selectors first
    |-- Fallback: textarea, contenteditable, input[type=text]
    |
    v
Type query character-by-character
    |-- Random delay: 15-45ms per char (anti-bot)
    |-- Press Enter to submit
    |
    v
[Response Polling Loop]
    |-- Check primary selectors for response text
    |-- Track content stability (3 stable ticks)
    |-- Minimum response length: 80 chars
    |-- Engine-specific timeout: 45-60s
    |
    v
Extract response text + cited URLs
```

### Proxy Management

```
+------------------+
| CloudProxy API   |
| GET /             |
+--------+---------+
         |
         v
+--------+---------+
| Proxy Router     |
|                  |
| Auth engines:    |     Sticky IP (1hr cache)
|  ChatGPT         +---> Same proxy per engine per hour
|  Claude          |     Maintains session cookies
|  Gemini          |
|  Perplexity      |
|                  |
| Unauth engines:  |     Rotating IP (per request)
|  Google AIO      +---> Different proxy each scrape
|  Google AI Mode  |     Avoids rate limits
+------------------+
```

- **Sticky proxies** for authenticated engines - session cookies are tied to IP address
- **Rotating proxies** for unauthenticated engines - distribute requests across IPs
- Proxy list fetched from CloudProxy API, cached locally
- SERP crawler uses sticky proxy with 30-minute TTL for consistent Google sessions
- Fallback: direct connection if no proxy configured

### Anti-Detection Measures

| Measure | Implementation |
|---------|---------------|
| Browser fingerprint | Camoufox (modified Firefox with anti-detection) |
| Headless mode | `"virtual"` - renders off-screen, not detectable |
| Human simulation | `humanize: True` - realistic mouse/keyboard timing |
| WebRTC blocking | `block_webrtc: True` - prevents real IP leakage |
| OS spoofing | `os: "windows"` - consistent fingerprint |
| Typing simulation | Character-by-character with 15-45ms random delays |
| Request spacing | 5-15s between engines, 3-10s between keywords |
| Batch cooldown | 30-90s pause between batch cycles |

---

## Smart DOM Extraction

The `app/agent/smart_extract.py` module provides universal extraction helpers that work across all engines without hardcoded selectors.

### Extraction Pipeline

```
[Pre-snapshot] Capture page text before query submission
    |
    v
[Response Detection]
    |-- Strategy A: Primary selectors (engine-specific)
    |-- Strategy B: Content growth detection (diff-based)
    |
    v
[Stability Check] 3 consecutive equal-length readings
    |
    v
[Text Extraction]
    |-- Content diff: new lines not in pre-snapshot
    |-- DOM scan: largest new text block on page
    |-- Body diff fallback: full page text comparison
    |
    v
[Cleaning]
    |-- Remove sidebar/nav phrases (SIDEBAR_PHRASES set)
    |-- Remove pre-snapshot duplicate lines
    |-- Filter short noise lines (<3 chars)
    |
    v
[URL Extraction] - cited source links
    |-- Collect all <a href="http..."> elements
    |-- Exclude site-internal domains
    |-- Deduplicate, limit to 20
```

### Key Design Decisions

- **No hardcoded response selectors** - uses content diffing so scrapers survive UI redesigns
- **SIDEBAR_PHRASES filter** - removes common navigation text (e.g., "new chat", "recents", "settings")
- **Stability polling** - waits for response to finish generating before extracting
- **Multi-strategy fallback** - if primary selectors miss, diff-based extraction catches it

---

## LLM Response Parsing

### Provider Fallback Chain

```
[Raw Response Text]
    |
    v
[Primary: Groq] (llama-3.3-70b-versatile, free tier)
    |-- If fail:
    v
[Fallback: OpenAI] (gpt-4o-mini)
```

### Extraction Schema

The LLM receives the raw engine response and extracts structured data:

```json
{
  "brand_mentions": [
    {
      "rank_position": 1,
      "brand_name": "GrabOn",
      "sentiment": "Positive",
      "context_snippet": "GrabOn offers the best coupon codes...",
      "cited_url": "https://www.grabon.in/ajio-coupons/"
    }
  ],
  "ai_hallucinated_coupons": [
    {
      "coupon_code": "AJIO50OFF",
      "associated_merchant": "AJIO",
      "status_flag": "Active-Valid"
    }
  ]
}
```

### Regex Brain (Learned Patterns)

The parser maintains a learned brand dictionary built from LLM parses. When the LLM identifies a brand name, the regex brain remembers it. On subsequent parses, known brands are detected via fast regex before falling back to LLM, reducing API costs.

### Coupon Status Classification

| Status | Meaning |
|--------|---------|
| `Active-Valid` | Code confirmed working on merchant site |
| `Expired-On-Site` | Code listed on merchant site but expired |
| `Hallucinated` | Code fabricated by the AI, not found anywhere |

---

## Pipeline Orchestration

### Single Keyword Pipeline

```python
run_pipeline(prompt, engine, country)
```

```
[1] Scrape engine for query text
    |-- On scrape error: track failure, trigger cooldown
    |-- On auth error: attempt reactive cookie refresh
    |-- On success: reset failure counter
    |
    v
[2] Parse raw text with LLM
    |-- Extract brand mentions + coupons
    |-- On parse failure: retry (up to 3x)
    |
    v
[3] Commit to database
    |-- INSERT execution_log
    |-- INSERT brand_mentions (per brand found)
    |-- INSERT ai_hallucinated_coupons (per coupon found)
    |
    v
[4] SSE broadcast results to connected clients
```

### Continuous Loop (Oldest-First Processing)

```
run_continuous_loop()
    |
    [forever]
    |
    v
Fetch batch of keywords, ordered by last_run_at ASC NULLS FIRST
    |-- Batch size: configurable (default 150)
    |-- Never-scraped keywords processed first
    |-- No tier weighting or priority classes
    |-- Shuffled within batch for anti-detection
    |
    v
For each prompt in batch:
    |-- For each available engine:
    |       |-- Skip if engine is in cooldown
    |       |-- Run pipeline (parallel per engine, semaphore-limited)
    |       |-- Skip if fresh data exists (within freshness_hours)
    |       |-- Dedup cache prevents re-scraping identical responses
    |-- Delay between engines for anti-detection
    |
    v
Batch cooldown: 30-90s
    |
    v
[Loop back]
```

### Parallel Engine Execution

Engines run concurrently per keyword (not sequentially). `run_parallel_engines()` launches all 6 engines simultaneously with per-engine concurrency limits:

```
Keyword "ajio coupons"
    |
    +---> [ChatGPT worker pool]    (4 concurrent)
    +---> [Gemini worker pool]     (4 concurrent)
    +---> [Perplexity worker pool] (4 concurrent)
    +---> [Claude worker pool]     (4 concurrent)
    +---> [Google AIO worker pool] (4 concurrent)
    +---> [AI Mode worker pool]    (4 concurrent)
    |
    v (all engines complete)
Next keyword
```

### Engine Cooldown & Auto-Heal

When an engine accumulates failures:

```
Failures:  1    2    3     4      5      6      7+
           |    |    |     |      |      |      |
Cooldown:  --   --   5m   10m   20m    40m    60m (max)
                     Exponential: BASE * 2^(n-3)
                     BASE = 300s, MAX = 3600s
```

**Auto-heal** triggers after consecutive fail threshold:
1. Try reactive cookie refresh
2. If that fails, attempt auto-relogin
3. If relogin succeeds, clear cooldown and resume
4. If all fail, engine stays on cooldown until manual intervention

Rate-limited engines enter 30-minute cooldown immediately.

---

## SERP Crawler

### Overview

Separate background loop (`run_serp_loop()`) crawls Google organic search results for every tracked keyword. Provides traditional SEO ranking data alongside AI engine visibility.

**File**: `app/agent/serp_scheduler.py`

### SERP Crawl Flow

```
run_serp_loop()
    |
    [forever]
    |
    v
Fetch batch of prompts (oldest last_serp_at first, NULLS FIRST)
    |-- Batch size: configurable (default 100)
    |
    v
For each keyword:
    |
    +---> [curl proxy request] Google search HTML
    |       |-- Success: parse with BeautifulSoup
    |       |-- 429 rate limit: fall back to browser
    |       |
    |       v
    +---> [Browser fallback] Camoufox loads Google search page
    |       |-- Parse rendered HTML
    |
    v
[BeautifulSoup Parser]
    |-- Extract organic results (rank, title, snippet, URL, domain)
    |-- Detect SERP features (PAA, answer box, featured snippets)
    |-- Flag target domain (is_target = TRUE)
    |-- Extract has_table for structured content detection
    |
    v
[DB Commit]
    |-- INSERT serp_results (metadata)
    |-- INSERT serp_organic_entries (per organic result)
    |-- INSERT serp_features (PAA, answer box, etc.)
    |-- UPDATE prompts SET last_serp_at = NOW()
    |
    v
[Delay] 5-12s between keywords (anti-rate-limit)
```

### SERP Proxy Strategy

- **Primary**: curl-based requests through rotating proxy (fast, low overhead)
- **Fallback**: Camoufox browser when curl gets 429 rate-limited
- **Sticky proxy**: 30-minute TTL for consistent Google sessions
- **Batch delay**: 5-12s random delay between keywords

---

## Citation & Content Analysis

### Content Fetcher (`app/agent/content_fetcher.py`)

Lightweight httpx-based page fetcher. Extracts:
- Title, meta description
- H1 tags, heading structure
- Word count, main text preview (5000 chars)
- JSON-LD schema types
- Canonical URL
- HTTP status

Results cached in `citation_pages` table with 24h freshness.

### Content Comparator (`app/agent/content_comparator.py`)

LLM-powered comparison of competitor pages vs target brand pages. Uses **cache-only** page lookups via `get_cached_page()` -- no live HTTP fetching during comparison or diagnosis.

```
[Competitor page (cited by AI)]  vs  [Target page (our brand)]
    |                                     |
    v                                     v
[Read from citation_pages cache]   [Read from citation_pages cache]
    |                                     |
    +---> Sent to gpt-4o-mini together <--+
                    |
                    v
          [Structured Analysis]
          - Content gaps
          - Structural advantages
          - Authority signals
          - Specific recommendations
          - Confidence score
```

**Two modes**:
1. **With target page**: direct comparison of both pages
2. **Without target page**: analyzes what makes the competitor effective, recommends what target brand should create

Pages not yet in cache are skipped (not fetched live). Cache populated by normal scraping/fetching cycles.

### Citation-SERP Overlaps (`build_citation_overlaps()`)

Cross-references AI-cited URLs with Google SERP organic rankings:

```
AI Citations (brand_mentions.cited_url)
    |
    v
Match against SERP results:
    |-- Exact URL match
    |-- Domain-level match (fallback)
    |-- Subdomain fuzzy match (last resort)
    |
    v
citation_serp_overlaps table
    |-- Which AI-cited URLs also rank in Google
    |-- What their Google organic rank is
    |-- Correlation between AI citation and SERP authority
```

### Integration with Diagnosis

The content comparator and citation overlap builder are fully integrated into the diagnosis engine:
- `build_citation_overlaps()` runs before every diagnosis to ensure fresh overlap data
- `analyze_citations_for_prompt()` runs on top 3 competitor pages during diagnosis
- Structured gap analysis (content gaps, structural advantages, authority signals) feeds directly into the diagnosis LLM prompt

---

## Diagnosis Engine

### Overview

The diagnosis engine is the core intelligence layer. It answers: **"WHY does AI rank competitors above you for this keyword, and WHAT specifically should you fix?"**

**File**: `app/agent/diagnosis_engine.py`

### Design Principle: DB-Only Evidence

All evidence is sourced exclusively from already-scraped GEO data in the database. No live HTTP fetching occurs during diagnosis. This ensures speed, reliability, and that every claim is backed by actual collected data.

### Evidence Gathering (7 parallel sources, all DB-only)

```
generate_diagnosis(prompt_id, keyword)
    |
    v
[1] Refresh citation-SERP overlaps (build_citation_overlaps)
    |
    v
[2] Gather evidence in parallel (asyncio.gather + per-source error isolation):
    |
    +---> _gather_ai_evidence()
    |     Source: brand_mentions + execution_logs (last 7 days)
    |     Data: per-engine rankings, sentiment, cited URLs, context snippets
    |
    +---> _gather_raw_ai_responses()       [NEW]
    |     Source: execution_logs.raw_response_text (last 7 days)
    |     Data: actual text AI engines returned, 1500 chars/engine (deduplicated)
    |     Richest source -- competitor names, pricing, features, reasoning
    |
    +---> _gather_coupon_evidence()         [NEW]
    |     Source: ai_hallucinated_coupons + execution_logs (last 7 days)
    |     Data: coupons mentioned by AI, valid vs hallucinated status per engine
    |
    +---> _gather_serp_evidence()
    |     Source: serp_organic_entries + serp_results (latest crawl)
    |     Data: Google organic rankings top 15, target brand position
    |
    +---> _gather_overlap_evidence()
    |     Source: citation_serp_overlaps (last 7 days, rebuilt in step 1)
    |     Data: AI-cited URLs that also rank in Google SERP
    |
    +---> _gather_content_evidence()        [REWRITTEN: DB-only, batch query]
    |     Source: citation_pages cache (no live fetching)
    |     Data: word count, schema types, H1 tags, content preview
    |     Single batch query for all URLs (no N+1)
    |
    +---> _gather_comparison_evidence()     [REWRITTEN: cache-only]
          Source: LLM content comparison via content_comparator
          Uses get_cached_page() -- reads citation_pages only, skips uncached
          Data: content gaps, structural advantages, authority signals (top 3)
```

### Error Isolation

Each evidence source wrapped in `_safe_gather()`. If one source fails (missing table, DB error, timeout), other 6 still return data. Failed sources fall back to descriptive empty strings detected by completeness tracker.

### Evidence Completeness Tracking

Before sending to LLM, engine checks which sources have data:

| Threshold | Behavior |
|-----------|----------|
| < 2 sources with data | Diagnosis refused. Returns `insufficient_data` error |
| 2-4 sources | Diagnosis generated with low confidence, data_gaps listed |
| 5-7 sources | Full diagnosis with proportional confidence |

Confidence cap formula: `min(raw_llm_confidence, sources_with_data / 7.0 + 0.15)`

### Target Status Classification

Based on AI evidence, the target brand is classified as:

| Status | Criteria |
|--------|----------|
| ABSENT | Not mentioned in any AI engine response |
| WEAK | Average rank > 3, often below competitors |
| MODERATE | Average rank 2-3, some negative mentions |
| GOOD | Average rank <= 2, mostly positive/neutral |

### LLM Analysis

All 7 evidence sources + completeness report sent to **gpt-4o-mini** (60s timeout):

```
Prompt structure:
  - ## KEYWORD
  - ## AI ENGINE DATA (per-engine rankings + sentiment)
  - ## RAW AI RESPONSES (actual text AI engines returned)
  - ## COUPON DATA (valid vs hallucinated coupons per engine)
  - ## SERP DATA (Google organic top 15)
  - ## CITATION OVERLAP (AI-cited URLs in Google SERP)
  - ## CONTENT COMPARISON (cached page data from citation_pages)
  - ## STRUCTURED GAP ANALYSIS (LLM competitor vs target comparison)
  - ## TARGET BRAND status + domain
  - ## EVIDENCE COMPLETENESS (which sources have data, which are empty)
```

**Critical LLM Rules (enforced in prompt)**:
1. ONLY state facts from evidence sections. Never assume or fabricate.
2. Every root_cause.evidence MUST quote a specific data point (URL, rank, word count, schema).
3. Every action_item MUST reference a real competitor URL or metric. Generic advice rejected.
4. confidence MUST reflect evidence completeness: 0.9+ only if all 7 sources have data.

### Post-Validation (`_validate_diagnosis_output`)

After LLM returns, output is validated before saving:

1. **Root causes**: dropped if evidence field empty or < 10 chars
2. **Action items**: dropped if matching generic phrases ("improve content quality", "build backlinks", etc.) AND no evidence_basis
3. **Confidence capping**: `min(raw_confidence, sources_with_data / 7.0 + 0.15)`
4. **Data gaps**: auto-populated from empty evidence sources
5. **Schema validation**: missing required fields get safe defaults (not crash)

### Output Schema

```json
{
  "root_causes": [
    {
      "category": "content_gap|authority_gap|freshness_gap|structural_gap|relevance_gap",
      "description": "specific description with competitor names and URLs",
      "evidence": "quotes specific data point from gathered evidence"
    }
  ],
  "action_items": [
    {
      "action": "specific step referencing real competitor data",
      "expected_impact": "high|medium|low",
      "effort": "high|medium|low",
      "target_url": "URL to modify or create",
      "evidence_basis": "which evidence data point supports this action"
    }
  ],
  "priority": "critical|high|medium|low",
  "confidence": 0.0-1.0,
  "summary": "one sentence referencing actual data",
  "data_gaps": ["list of evidence sources that were empty"]
}
```

### Error Returns

All error paths return structured dicts (not None):

| Error | Cause |
|-------|-------|
| `insufficient_data` | < 2 of 7 evidence sources have data |
| `no_api_key` | OPENAI_API_KEY not configured |
| `empty_response` | LLM returned empty text |
| `invalid_json` | LLM output not valid JSON |
| `generation_failed` | OpenAI API error, timeout, or other exception |

### Content Comparator (cache-only mode)

**File**: `app/agent/content_comparator.py`

Uses `get_cached_page()` from content_fetcher -- reads only from `citation_pages` DB table. If a page hasn't been fetched yet, it's skipped (not fetched live). Comparison quality improves as more pages get cached through normal scraping cycles.

Anti-fabrication rules enforced in comparator prompts:
- "ONLY reference facts visible in the content previews"
- "If content preview is truncated, say 'based on visible content'"
- System message: "Never assume or fabricate content not shown in the previews"

### Diagnosis Scheduler

Background loop in `app/agent/diagnosis_scheduler.py`:
- Runs daily, processes up to 20 keywords per cycle
- Priority scoring: ABSENT (100), Negative sentiment (80), Low rank (60), Moderate (40), Good (20)
- Keywords with no prior diagnosis or high priority score processed first
- 5s delay between diagnoses to respect LLM API rate limits
- Cost tracked per diagnosis in `api_costs` table

### Diagnosis Lifecycle

```
[New] --> [Active] --> [Superseded] (when re-diagnosed)
                  |
                  +--> [Verified Effective] (fix worked)
                  +--> [Verified Ineffective] (fix didn't work)
                  +--> [Verified No Impact] (no change)
```

---

## Verification Loop

### Overview

Tracks whether fixes applied based on diagnoses actually improve metrics. Compares before/after data across multiple dimensions.

**File**: `app/agent/verification.py`

### Verification Flow

```
[Fix Applied] (recorded in applied_fixes table)
    |
    v
[Wait for 3+ post-fix scrape cycles]
    |
    v
check_fix_impact(fix_id)
    |
    v
[Compare Before vs After the fix date]
    |
    +---> AI rank position (avg before vs avg after)
    +---> AI mention count (total before vs after)
    +---> Positive sentiment % (before vs after)
    +---> SERP organic rank (target domain before vs after)
    +---> Target cited in AI (boolean before vs after)
    |
    v
[Verdict]
    |-- improved: more metrics improved than degraded
    |-- degraded: more metrics degraded than improved
    |-- no_change: equal improved/degraded
    |-- insufficient_data: fewer than 3 post-fix datapoints
    |
    v
[Update applied_fixes]
    |-- verification_status = verdict
    |-- verification_result = full metric snapshot (JSONB)
    |-- last_verified_at = NOW()
    |
    v
[Backlink to Diagnosis] (feedback loop)
    |-- Update diagnosis status:
    |     improved --> "verified_effective"
    |     degraded --> "verified_ineffective"
    |     no_change --> "verified_no_impact"
    |-- Store verification metadata in diagnosis evidence_snapshot
    |-- Record which metrics improved (validated_metrics)
```

### Automated Verification

- `verify_all_monitoring_fixes()` runs every 6 hours via scheduler
- Re-verifies all fixes with status `monitoring`
- Skips fixes that still have `insufficient_data`
- On-demand verification available via `POST /api/fixes/{fix_id}/verify`

### Fix Timeline

`get_fix_timeline(fix_id)` returns time-series data for before/after visualization:
- AI rank position over time (per engine)
- SERP organic rank over time
- Fix application date as divider

---

## Database Schema

### Entity Relationships

```
prompts (keywords to monitor)
    |
    | 1:N                    1:N
    v                        v
execution_logs           serp_results
(AI engine scrapes)      (Google SERP crawls)
    |                        |
    | 1:N        1:N         | 1:N           1:N
    v            v           v               v
brand_mentions  coupons  serp_organic    serp_features
                         _entries        (PAA, etc.)
    |
    | N:1
    v
citation_serp_overlaps  <--  serp_organic_entries
    |
    v
diagnoses (root cause analysis)
    |
    | 1:N
    v
applied_fixes (recorded fixes)
    |-- verification_status
    |-- verification_result (JSONB)
    |-- backlinks to diagnosis status
```

### Key Tables

```sql
-- Keywords to track
CREATE TABLE prompts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text TEXT NOT NULL UNIQUE,
    merchant_category VARCHAR(50) NOT NULL,
    intent_type VARCHAR(30) NOT NULL,
    tier INTEGER DEFAULT 2,           -- metadata only, not used for processing
    keyword_group VARCHAR(200),
    is_canonical BOOLEAN DEFAULT TRUE,
    last_run_at TIMESTAMPTZ,          -- last AI engine scrape
    last_serp_at TIMESTAMPTZ,         -- last SERP crawl
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- AI engine scrape results
CREATE TABLE execution_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
    engine_name VARCHAR(30) NOT NULL,
    country_code VARCHAR(5) DEFAULT 'IN',
    raw_response_text TEXT NOT NULL,
    captured_at TIMESTAMPTZ DEFAULT NOW()
);

-- Extracted brand positions from AI responses
CREATE TABLE brand_mentions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    log_id UUID REFERENCES execution_logs(id) ON DELETE CASCADE,
    rank_position INT NOT NULL,
    brand_name VARCHAR(100) NOT NULL,
    is_target_brand BOOLEAN DEFAULT FALSE,
    sentiment VARCHAR(10) NOT NULL,
    context_snippet TEXT,
    cited_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Google SERP crawl results
CREATE TABLE serp_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
    device VARCHAR(10) DEFAULT 'desktop',
    results_count TEXT,
    raw_html_hash VARCHAR(64),
    captured_at TIMESTAMPTZ DEFAULT NOW()
);

-- Individual organic SERP entries
CREATE TABLE serp_organic_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    serp_id UUID REFERENCES serp_results(id) ON DELETE CASCADE,
    rank_position INT NOT NULL,
    title TEXT NOT NULL,
    snippet TEXT,
    url TEXT NOT NULL,
    domain VARCHAR(200) NOT NULL,
    has_table BOOLEAN DEFAULT FALSE,
    is_target BOOLEAN DEFAULT FALSE
);

-- SERP features (PAA, answer box, etc.)
CREATE TABLE serp_features (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    serp_id UUID REFERENCES serp_results(id) ON DELETE CASCADE,
    feature_type VARCHAR(30) NOT NULL,
    rank_position INT,
    title TEXT,
    snippet TEXT,
    url TEXT,
    domain VARCHAR(200),
    question_text TEXT
);

-- Fetched citation page content (24h cache)
CREATE TABLE citation_pages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    url TEXT NOT NULL,
    domain VARCHAR(200) NOT NULL,
    title TEXT,
    meta_description TEXT,
    h1_tags TEXT[],
    word_count INT,
    main_text_preview TEXT,
    schema_types TEXT[],
    canonical_url TEXT,
    fetch_status INT DEFAULT 0,
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);

-- AI citation vs SERP overlap tracking
CREATE TABLE citation_serp_overlaps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
    engine_name VARCHAR(30) NOT NULL,
    cited_url TEXT NOT NULL,
    serp_rank INT,
    brand_mention_id UUID REFERENCES brand_mentions(id),
    serp_entry_id UUID REFERENCES serp_organic_entries(id),
    detected_at TIMESTAMPTZ DEFAULT NOW()
);

-- Diagnosis results
CREATE TABLE diagnoses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
    engine_name VARCHAR(30) NOT NULL,
    root_causes JSONB NOT NULL DEFAULT '[]',
    action_items JSONB NOT NULL DEFAULT '[]',
    priority VARCHAR(10) DEFAULT 'medium',
    confidence FLOAT DEFAULT 0.0,
    summary TEXT DEFAULT '',
    evidence_snapshot JSONB,          -- includes verification backlink data
    llm_model VARCHAR(60),
    cost_usd NUMERIC(10,6) DEFAULT 0,
    status VARCHAR(20) DEFAULT 'active',  -- active/superseded/verified_*
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Applied fixes with verification tracking
CREATE TABLE applied_fixes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    diagnosis_id UUID REFERENCES diagnoses(id) ON DELETE SET NULL,
    prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
    engine_name VARCHAR(30) DEFAULT 'all',
    description TEXT DEFAULT '',
    target_url TEXT,
    applied_at TIMESTAMPTZ DEFAULT NOW(),
    verification_status VARCHAR(20) DEFAULT 'monitoring',
    last_verified_at TIMESTAMPTZ,
    verification_result JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- API cost tracking
CREATE TABLE api_costs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider VARCHAR(30) NOT NULL,
    model VARCHAR(60) NOT NULL,
    input_tokens INT DEFAULT 0,
    output_tokens INT DEFAULT 0,
    cost_usd NUMERIC(10,6) DEFAULT 0,
    purpose VARCHAR(30) DEFAULT 'parser',  -- parser/diagnosis/comparison
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Performance Indexes

```sql
-- Execution logs
CREATE INDEX idx_execution_logs_prompt_engine_captured ON execution_logs (prompt_id, engine_name, captured_at DESC);
CREATE INDEX idx_execution_logs_captured_at ON execution_logs (captured_at DESC);
CREATE INDEX idx_execution_logs_engine_captured ON execution_logs (engine_name, captured_at DESC);

-- Brand mentions
CREATE INDEX idx_brand_mentions_log_id ON brand_mentions (log_id);
CREATE INDEX idx_brand_mentions_brand_lower ON brand_mentions (LOWER(brand_name));

-- Prompts
CREATE INDEX idx_prompts_tier_canonical_lastrun ON prompts (tier, is_canonical, last_run_at NULLS FIRST);
CREATE INDEX idx_prompts_last_serp_at ON prompts (last_serp_at ASC NULLS FIRST);
CREATE INDEX idx_prompts_intent_type ON prompts (intent_type);

-- SERP
CREATE INDEX idx_serp_results_prompt_captured ON serp_results (prompt_id, captured_at DESC);
CREATE INDEX idx_serp_results_captured_at ON serp_results (captured_at DESC);
CREATE INDEX idx_serp_organic_domain ON serp_organic_entries (domain);
CREATE INDEX idx_serp_organic_serp_id ON serp_organic_entries (serp_id);
CREATE INDEX idx_serp_organic_is_target ON serp_organic_entries (serp_id, is_target) WHERE is_target = TRUE;
CREATE INDEX idx_serp_features_serp_id ON serp_features (serp_id);

-- Citations
CREATE UNIQUE INDEX idx_citation_pages_url ON citation_pages (url);
CREATE INDEX idx_citation_overlap_prompt ON citation_serp_overlaps (prompt_id, engine_name);

-- Diagnoses
CREATE INDEX idx_diagnoses_prompt_engine ON diagnoses (prompt_id, engine_name, created_at DESC);
CREATE INDEX idx_diagnoses_priority ON diagnoses (priority, status, created_at DESC);

-- Applied fixes
CREATE INDEX idx_fixes_status ON applied_fixes (verification_status, last_verified_at);
CREATE INDEX idx_fixes_prompt_engine ON applied_fixes (prompt_id, engine_name);

-- API costs
CREATE INDEX idx_api_costs_created ON api_costs (created_at DESC);
```

---

## Performance Optimizations

### Server-Side TTL Cache

Module-level dict-based cache in `app/routes/api.py` to avoid redundant DB queries:

```python
_cache: dict[str, tuple[float, object]] = {}

def _cached(key: str, ttl: int = 30):
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < ttl:
        return entry[1]
    return None
```

| Endpoint Category | TTL | Reason |
|-------------------|-----|--------|
| Pipeline progress | 10s | Needs near-real-time accuracy |
| Dashboard KPIs | 30s | Moderate freshness requirement |
| Rankings live | 30s | Paginated, frequently polled |
| SERP stats | 15s | Polled every 15s by frontend |
| Analytics endpoints | 45-60s | Heavy CTE queries, data changes slowly |
| Competitor analysis | 60s | Expensive aggregations |

**Impact**: 45x speedup on cached hits (2.8s -> 62ms for analytics endpoints).

### Client-Side Tab Preloading

SERP page preloads all tab data 2 seconds after initial page load:

```javascript
setTimeout(() => {
    // Preload all inactive tabs in background
    loadMovementCards(); loadSeoDistribution();
    loadCompetitors(); loadSerpFeatures();
    loadOverlaps(); loadAiMovement();
    loadAiSerpCategories(); loadAiSerpMatrix(1);
}, 2000);
```

Combined with server cache, tab switching is instant after initial load.

### Database Index Strategy

5 targeted indexes added for the heaviest query patterns:
- `idx_brand_mentions_brand_lower` - LOWER() queries on brand names
- `idx_prompts_last_serp_at` - SERP scheduler ordering
- `idx_prompts_intent_type` - intent-based filtering
- `idx_serp_results_captured_at` - SERP rate calculation
- `idx_serp_organic_is_target` - partial index for target-only lookups

---

## Real-Time Event System

### SSE Architecture

```
[Pipeline Event]
    |
    v
broadcast(event_type, **data)
    |-- Pushes to in-memory event queue
    |
    v
[SSE Endpoint: /api/activity-stream]
    |-- Long-lived HTTP connection
    |-- Server pushes events as they happen
    |
    v
[Browser EventSource]
    |-- Receives events
    |-- Updates dashboard in real-time
    |-- No polling needed
```

### Event Types

| Event | Payload | Use |
|-------|---------|-----|
| `scrape_start` | engine, prompt, attempt | Show "scraping..." indicator |
| `scrape_done` | engine, prompt, chars, urls | Update stats live |
| `scrape_fail` | engine, prompt, error | Show error notifications |
| `parse_done` | engine, prompt, brands, coupons | Trigger data refresh |
| `batch_start` | total_prompts, engines | Batch progress tracking |
| `batch_done` | processed | Trigger dashboard refresh |
| `engine_recovery` | engine, action | Show recovery notifications |
| `agent_waiting` | reason, resume_in, cooldowns | Show cooldown status |
| `auto_login` | provider, step, message | Auth recovery status |
| `diagnosis_start` | count | Diagnosis batch beginning |
| `diagnosis_done` | keyword, priority | Individual diagnosis complete |
| `diagnosis_batch_done` | success, total | Diagnosis batch summary |
| `serp_done` | keyword, organic_count | SERP crawl complete |

---

## API Surface

### Dashboard Pages

| Path | Template | Description |
|------|----------|-------------|
| `/` | `dashboard.html` | Main dashboard with KPIs, rankings, live activity feed |
| `/serp` | `serp.html` | SERP rankings, AI-SERP overlap, movement analysis |
| `/analytics` | `analytics.html` | Visibility trends, competitor analysis, heatmaps |
| `/diagnosis` | `diagnosis.html` | Root cause analysis cards, action items |
| `/verification` | `verification.html` | Fix tracking, before/after timelines |
| `/keywords` | `keywords.html` | Keyword management, import/export |
| `/logs` | `logs.html` | Execution logs, response viewer |
| `/red-flags` | `red_flags.html` | Hallucinated coupons, negative sentiment alerts |

### Control Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/scrape-now` | Trigger immediate scrape for a keyword |
| POST | `/api/trigger-batch` | Run manual batch (oldest-first) |
| POST | `/api/reparse` | Re-parse unparsed execution logs |
| POST | `/api/clear-cooldown/{engine}` | Reset engine cooldown |
| POST | `/api/test-engine` | Test single engine for a keyword |

### GEO Analytics Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/rankings-live` | Rankings matrix (paginated, filterable) |
| GET | `/api/dashboard-kpis` | Share of voice, mentions, coverage |
| GET | `/api/engine-health` | Per-engine stats, streaks, cooldowns |
| GET | `/api/pipeline-progress` | Overall + per-engine scrape progress |
| GET | `/api/analytics/summary` | SoV trends, mention counts |
| GET | `/api/analytics/keyword-detail` | Per-keyword history across engines |
| GET | `/api/analytics/rank-movement` | Rank changes over time |
| GET | `/api/analytics/visibility` | AI visibility score breakdown |
| GET | `/api/analytics/competitors` | Top competitor analysis |
| GET | `/api/analytics/weaknesses` | Keywords where target is weak/absent |
| GET | `/api/analytics/sentiment` | Sentiment distribution |
| GET | `/api/analytics/keyword-gaps` | Keywords with no target presence |
| GET | `/api/analytics/heatmap` | Engine x keyword rank heatmap |
| GET | `/api/analytics/ai-serp-matrix` | AI rank vs SERP rank correlation |

### SERP Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/serp/stats` | SERP crawl progress and rate |
| GET | `/api/serp/rankings` | SERP organic rankings (paginated) |
| GET | `/api/serp/movement-cards` | Rank gainers/losers |
| GET | `/api/serp/rank-distribution-seo` | Rank position distribution |
| GET | `/api/serp/top-competitors` | Top SERP domains |
| GET | `/api/serp/feature-summary` | SERP feature frequency |
| GET | `/api/serp/overlaps` | AI citation vs SERP overlap data |

### Diagnosis Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/diagnoses` | List all diagnoses (filterable) |
| GET | `/api/diagnoses/{id}` | Full diagnosis with evidence |
| POST | `/api/diagnose/{prompt_id}` | Trigger on-demand diagnosis |
| GET | `/api/diagnosis-priority` | Priority queue for diagnosis |

### Verification Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/fixes` | List applied fixes with status |
| GET | `/api/fixes/{id}` | Fix details + verification result |
| POST | `/api/fixes` | Record a new applied fix |
| POST | `/api/fixes/{id}/verify` | Trigger on-demand verification |
| GET | `/api/fixes/{id}/timeline` | Before/after time-series data |
| DELETE | `/api/fixes/{id}` | Delete fix record |

### Citation Analysis Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/citation-analyze/{prompt_id}` | Run content comparison for a keyword |

### Health & Monitoring

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/activity-stream` | SSE event stream |
| GET | `/api/log-response/{id}` | Raw response for a log entry |
| GET | `/api/auth/status/{provider}` | Cookie health per provider |
| GET | `/api/cost-summary` | API cost breakdown |

### AI Visibility Score

```
AI Visibility = SoV * 0.4 + CitationRate * 0.3 + PositiveRate * 0.2 + Coverage * 0.1

Where:
  SoV = grabon_weight / (grabon_weight + competitor_weight) * 100
  CitationRate = cited_mentions / total_mentions * 100
  PositiveRate = positive_mentions / total_mentions * 100
  Coverage = engines_with_mentions / total_engines * 100

  weight = sum(1 / rank_position) for each mention
```

---

## Authentication & Cookie Management

### Auth Flow

```
User clicks "Login" for an engine in sidebar
    |
    v
POST /api/auth/start/{provider}
    |-- Launch Camoufox browser
    |-- Navigate to login page
    |-- Return session_id
    |
    v
[Interactive Login] (user controls browser remotely)
    |-- GET /api/auth/screenshot/{session_id} (live view)
    |-- POST /api/auth/click (send click events)
    |-- POST /api/auth/type (send keystrokes)
    |
    v
[Cookies Captured]
    |-- Extract cookies from browser context
    |-- Save to app_settings table as JSONB
    |
    v
[Auto-Refresh Schedule]
    |-- Every 6 hours: refresh all engine cookies
    |-- Every 3 hours: refresh Perplexity cookies (shorter TTL)
    |-- Every 15 minutes: validate cookie health
    |-- On failure: reactive refresh + auto-relogin attempt
```

### Supported Auth Providers

| Provider | Login URL | Cookie Storage Key | Refresh Interval |
|----------|-----------|-------------------|------------------|
| Google | google.com | google_cookies | 6h |
| ChatGPT | auth.openai.com | chatgpt_cookies | 6h |
| Perplexity | perplexity.ai | perplexity_cookies | 3h |
| Gemini | accounts.google.com | gemini_cookies | 6h |
| Claude | claude.ai/login | claude_cookies | 6h |

### Multi-Account Pool

Support for multiple account slots per engine (`max_account_slots = 5`). Account pools initialized at startup for load distribution.

---

## Resilience & Error Handling

### Failure Recovery Matrix

| Failure Type | Detection | Recovery |
|-------------|-----------|----------|
| Scrape timeout | No response in max_wait | Retry (up to 3x) |
| Auth expired | Redirect to login page | Reactive cookie refresh, then auto-relogin |
| Rate limited | HTTP 429 / captcha | Engine cooldown (exponential backoff) |
| Proxy failure | Connection error | Rotate proxy, retry |
| Parse failure | No valid JSON from LLM | Fallback to next provider |
| DB commit error | psycopg exception | Slack alert, rollback |
| Browser crash | WriteUnixTransport error | Silently ignored (data captured) |
| All engines down | No available engines | Wait for soonest recovery |
| Popup blocking | Input element not found | Multi-strategy dismissal |
| SERP 429 | Google rate limit on curl | Fall back to browser-based crawl |
| Content fetch fail | httpx error | Log warning, skip comparison |
| Diagnosis LLM error | Invalid JSON / API error | Log error, return None |

### Slack Alerts

Database commit failures trigger Slack notifications via webhook:
- Message includes: error details, engine, keyword
- Configured via `SLACK_WEBHOOK_URL` environment variable

---

## Configuration Reference

All settings managed via `app/config.py` using Pydantic BaseSettings (environment variables).

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/grabon_geo` | PostgreSQL connection |
| `OPENAI_API_KEY` | `""` | OpenAI API key (parser + diagnosis + comparison) |
| `GROQ_API_KEY` | `""` | Groq API key (parser, free) |
| `CLOUDPROXY_URL` | `""` | CloudProxy API endpoint |
| `SLACK_WEBHOOK_URL` | `""` | Slack webhook for alerts |
| `TARGET_DOMAIN` | `grabon.in` | Target brand domain for SERP tracking |

### Pipeline Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CONTINUOUS_BATCH_SIZE` | `150` | Keywords per loop cycle |
| `CONTINUOUS_CONCURRENCY` | `6` | Max parallel keyword processing |
| `PARALLEL_ENGINES` | `true` | Run all engines concurrently per keyword |
| `CONCURRENCY_PER_ENGINE` | `4` | Max concurrent scrapes per engine |
| `SKIP_FRESH_DATA` | `true` | Skip keywords scraped within freshness window |
| `FRESHNESS_HOURS` | `24` | Hours before data is considered stale |
| `USE_DEDUP_CACHE` | `true` | Skip duplicate responses |

### SERP Crawler Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SERP_BATCH_SIZE` | `100` | Keywords per SERP crawl cycle |
| `SERP_INTERVAL_HOURS` | `12` | Hours between full SERP cycles |
| `SERP_CONCURRENCY` | `3` | Parallel SERP crawls |
| `SERP_DELAY_MIN` | `5.0` | Min seconds between SERP requests |
| `SERP_DELAY_MAX` | `12.0` | Max seconds between SERP requests |
| `SERP_USE_CURL` | `true` | Use curl before browser fallback |

### Other Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `KEYWORDS_EXCEL_PATH` | `Public/Rankings Agent Keywords.xlsx` | Excel import path |
| `CREDENTIALS_KEY` | `grabon-geo-agent-creds-key-2025` | Encryption key for stored credentials |
| `MAX_ACCOUNT_SLOTS` | `5` | Max accounts per engine |
| `CRON_HOUR` | `0` | Scheduled sweep hour |
| `CRON_MINUTE` | `0` | Scheduled sweep minute |

---

## Deployment

### Prerequisites

- Python 3.10+
- PostgreSQL 16+
- 4+ GB RAM (concurrent browser instances)
- Camoufox (auto-installed via pip)

### Docker Compose

```yaml
services:
  web:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/grabon_geo
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - GROQ_API_KEY=${GROQ_API_KEY}
      - TARGET_DOMAIN=grabon.in
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: grabon_geo
      POSTGRES_PASSWORD: postgres
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d grabon_geo"]
      interval: 5s

volumes:
  pgdata:
```

### Startup Sequence

```
1. FastAPI app initializes
2. Database schema created/migrated (init_schema) -- 14 tables, 20+ indexes
3. Account pools and dedup cache initialized
4. APScheduler starts
5. Continuous GEO pipeline loop begins (oldest-first, all keywords)
6. SERP crawl loop begins (batch + interval based)
7. Diagnosis loop begins (daily, priority-scored)
8. Camoufox browser pre-warmed
9. Cookie refresh jobs scheduled (every 3-6h per engine)
10. Cookie health checks scheduled (every 15m)
11. Verification checks scheduled (every 6h)
12. SSE stream ready for client connections
```

### Background Loops

| Loop | File | Cadence | Description |
|------|------|---------|-------------|
| Continuous pipeline | `pipeline.py` | Continuous | GEO scraping, oldest-first batches |
| SERP crawler | `serp_scheduler.py` | Continuous (12h cycle) | Google organic rankings |
| Diagnosis | `diagnosis_scheduler.py` | Daily (20 keywords) | Root cause analysis |
| Verification | `verification.py` | Every 6h | Fix impact assessment |
| Cookie refresh | `auth.py` | Every 3-6h | Per-engine session maintenance |
| Cookie health | `auth.py` | Every 15m | Validate all sessions |

### Critical Thresholds

| Parameter | Value | Impact |
|-----------|-------|--------|
| Max retries per engine | 3 | Higher = more resilient, slower |
| Engine delay | 5-15s | Lower = faster, higher ban risk |
| Prompt delay | 3-10s | Lower = faster, higher ban risk |
| Batch cooldown | 30-90s | Prevents sustained high load |
| Proxy sticky TTL | 1 hour (engines), 30min (SERP) | Balance session vs rotation |
| Cookie refresh | 3-6 hours | More frequent = fewer auth failures |
| Health check | 15 minutes | Detect expired sessions early |
| Concurrent browsers | 4+ | Limited by RAM (~1GB each) |
| Diagnosis daily limit | 20 | LLM API cost control |
| Verification min datapoints | 3 | Ensures statistical significance |
| Cache TTL (API) | 10-60s | Balance freshness vs DB load |

---

*GrabOn GEO Agent v2.0 -- FastAPI + PostgreSQL + Camoufox + LLM Pipeline + SERP + Diagnosis + Verification*
