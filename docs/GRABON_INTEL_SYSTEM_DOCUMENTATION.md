# GrabOn Intel — Agentic Lead Intelligence Platform

## System Documentation v2.0

**Prepared by:** GrabOn Engineering  
**Last Updated:** May 27, 2026  
**Classification:** Internal — Engineering & Sales Operations

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture](#2-system-architecture)
3. [Lead Discovery — How Brands Are Found](#3-lead-discovery--how-brands-are-found)
4. [Signal Collection — Data Sources & Mediums](#4-signal-collection--data-sources--mediums)
5. [Research Pipeline — The 5-Node Intelligence Graph](#5-research-pipeline--the-5-node-intelligence-graph)
6. [Lead Qualification — Scoring & Tiering](#6-lead-qualification--scoring--tiering)
7. [Pre-Qualification Gate](#7-pre-qualification-gate)
8. [Approval System — Human-in-the-Loop](#8-approval-system--human-in-the-loop)
9. [Outreach Engine](#9-outreach-engine)
10. [Monitoring & Change Detection](#10-monitoring--change-detection)
11. [LLM Stack & Cost Control](#11-llm-stack--cost-control)
12. [Frontend Dashboard](#12-frontend-dashboard)
13. [Data Flow — End to End](#13-data-flow--end-to-end)
14. [Database Schema](#14-database-schema)
15. [Deployment & Infrastructure](#15-deployment--infrastructure)

---

## 1. Executive Summary

GrabOn Intel is a **fully autonomous, agentic lead intelligence system** purpose-built for identifying, qualifying, and engaging high-potential Indian D2C brands that need digital marketing services.

The system operates 24/7 with zero manual intervention for discovery. It:

- **Discovers** brands through 29 automated signal collectors scanning news, ads, job postings, funding announcements, and web infrastructure.
- **Researches** each brand through a 5-node AI supervisor that builds a structured dossier with company data, competitor landscape, opportunity assessment, quantified score, and personalized outreach.
- **Qualifies** leads on a 0–100 scale across 9 weighted dimensions, assigning them to tiers (Hot / Warm / Watchlist / Park).
- **Routes** high-potential leads (Hot/Warm) to a human approval queue before any outreach occurs.
- **Generates** personalized 5-touch cold email sequences grounded in factual data points from the research.
- **Monitors** tracked brands weekly for score drift, new funding, competitive shifts, and changed digital posture.

Every data point in the system is **evidence-backed** — the LLM nodes are explicitly instructed to never hallucinate, and scoring is calibrated with real collector signals as ground truth.

---

## 2. System Architecture

### Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Backend API** | FastAPI (Python 3.12+) | REST API, auth, CRUD |
| **Workflow Engine** | Temporal.io | Durable workflow orchestration, retries, scheduling |
| **AI Graph** | LangGraph | 5-node stateful research supervisor |
| **LLM Router** | LiteLLM | Multi-provider routing with tier fallback |
| **Database** | PostgreSQL 17 + pgvector | Signals, dossiers, embeddings, budget |
| **Search** | SearXNG (self-hosted) | Free SERP aggregation across engines |
| **Frontend** | Next.js 14 (React 18, TypeScript) | Dashboard, brand detail, approvals |
| **Observability** | Langfuse (optional) | LLM cost/latency tracking |

### Service Topology

```
┌──────────────────────────────────────────────────────────────┐
│                     Docker Compose Cluster                    │
│                                                              │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌────────────┐ │
│  │ Next.js  │  │  FastAPI   │  │ Temporal │  │  SearXNG   │ │
│  │ Frontend │──│  API       │──│  Server  │  │  (SERP)    │ │
│  │ :3000    │  │  :8000     │  │  :7233   │  │  :8888     │ │
│  └──────────┘  └─────┬─────┘  └────┬─────┘  └──────┬─────┘ │
│                      │             │                │       │
│                      │      ┌──────┴──────┐         │       │
│                      │      │  Workers    │─────────┘       │
│                      │      │  (×3)       │                 │
│                      │      │ - Collectors│                 │
│                      │      │ - LangGraph │                 │
│                      │      │ - Outreach  │                 │
│                      │      └──────┬──────┘                 │
│                      │             │                        │
│                 ┌────┴─────────────┴────┐                   │
│                 │    PostgreSQL 17      │                   │
│                 │    + pgvector         │                   │
│                 │    :5432              │                   │
│                 └──────────────────────┘                   │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Lead Discovery — How Brands Are Found

### Autonomous Discovery Loop

The `AutoDiscoveryWF` workflow runs on a configurable schedule (default: every 6 hours) and executes a multi-phase sweep to find new brands:

```
Phase 1 → News Polling (Google News RSS)
Phase 2 → Google Ads Transparency (active ad spenders)
Phase 3 → LinkedIn Jobs (hiring signals)
Phase 4 → Meta Ad Library (Facebook/Instagram advertisers)
Phase 5 → SEO Rank Tracking (organic search presence)
Phase 6 → Pre-Qualification Gate (quick score filter)
Phase 7 → Fan-out to DossierWF (full research for qualified brands)
```

### Discovery Queries (ICP Seed Queries)

Discovery is driven by **24 high-intent search queries** designed around GrabOn's ICP (Indian D2C brands needing marketing services):

| Category | Example Queries |
|----------|----------------|
| **Coupon/Offer Campaigns** | "Indian brand coupon code offers discount", "cashback coupon site India brand" |
| **D2C Categories** | "Indian D2C brand beauty skincare", "India fashion D2C startup", "Indian health wellness D2C brand" |
| **Funding Triggers** | "Indian D2C brand funding 2024 2025", "India direct-to-consumer startup Series A" |
| **Scaling Signals** | "new D2C brand launch India ecommerce", "D2C brand India revenue crore" |
| **Hiring Markers** | "performance marketing manager India D2C", "affiliate manager India brand" |
| **Competitive Gaps** | Brands on competing coupon platforms but NOT on GrabOn |

### ICP Pre-Filter (Automatic Rejection)

Before any brand enters the pipeline, a rule-based filter rejects:

- News sites, media outlets, government domains
- Educational institutions
- Marketplace aggregators (Amazon, Flipkart, etc.)
- SaaS/B2B tools
- Existing GrabOn competitors

---

## 4. Signal Collection — Data Sources & Mediums

### Overview

The system uses **29 automated signal collectors** that gather factual data from free, publicly available sources. No paid APIs are required for core operation.

Every collector produces `SignalEvent` objects stored in PostgreSQL with deduplication (unique `dedupe_key` constraint), ensuring idempotent re-runs.

### Discovery Collectors (Find New Brands)

| # | Collector | Data Source | What It Finds | Signal Types |
|---|-----------|-------------|---------------|-------------|
| 1 | `news_polling` | Google News RSS via SearXNG | Brand mentions in news — funding, growth, exec changes | `news.funding`, `news.growth`, `news.exec_change` |
| 2 | `google_ads_transparency` | SearXNG (ads queries) | Brands actively spending on Google Ads | `ad_spend.google.active` |
| 3 | `meta_ad_library` | Meta Ads Archive API | Brands running Facebook/Instagram ads | `ad_spend.meta.active` |
| 4 | `linkedin_jobs` | LinkedIn job postings via SERP | Brands hiring marketing roles (growth signal) | `hiring.signal` |
| 5 | `hn_discovery` | Hacker News API | Indie/bootstrap brand mentions | `news.hn_mention` |
| 6 | `cert_transparency` | CT Logs | New domain registrations (new brand launches) | `domain.new_cert` |

### Enrichment Collectors (Analyze Known Brands)

| # | Collector | Data Source | What It Measures | Signal Types |
|---|-----------|-------------|-----------------|-------------|
| 7 | `tech_stack` | Homepage HTML/headers | Ecommerce platform, analytics, payment gateways, frameworks | `tech.stack` |
| 8 | `pagespeed` | Google PageSpeed Insights API | Performance score, LCP, FCP, CLS, TBT | `site.pagespeed` |
| 9 | `social_presence` | Homepage meta tags + platform checks | Platform count, handles, influencer programs | `social.presence` |
| 10 | `content_blog` | Website scraping | Blog existence, post count, maturity level | `content.blog` |
| 11 | `email_maturity` | DNS records (SPF/DKIM/DMARC) | Email authentication, provider detection | `email.maturity` |
| 12 | `ads_txt` | ads.txt file parsing | Programmatic ad partnerships | `ads_programmatic.active` |
| 13 | `affiliate_program` | Website scraping (/affiliate, /partners) | Affiliate network detection | `marketing.affiliate_program` |
| 14 | `app_store` | Google Play Store | App rating, reviews, install count | `app.android` |
| 15 | `ios_app_store` | Apple App Store | App rating, reviews | `app.ios` |
| 16 | `youtube_social` | YouTube API (free tier) | Subscriber count, video count | `social.youtube` |
| 17 | `google_trends` | Google Trends | Search interest trend (rising/falling) | `search.trends` |
| 18 | `seo_rank_tracking` | SearXNG SERP positions | Keyword rankings, avg position | `seo.rank_tracking` |

### Funding & Company Data Collectors

| # | Collector | Data Source | What It Extracts | Signal Types |
|---|-----------|-------------|-----------------|-------------|
| 19 | `crunchbase_funding` | Crunchbase via SearXNG | Funding rounds, investors, profile URL | `funding.round`, `funding.total` |
| 20 | `tracxn_funding` | Tracxn via SearXNG | Funding rounds, revenue estimates, employee count | `funding.round`, `revenue.estimate`, `company.employees` |
| 21 | `tofler_company` | Tofler.in + Zaubacorp via SearXNG | Indian MCA filings: revenue, capital, directors, CIN | `company.revenue_filing`, `company.capital`, `company.employees`, `company.directors`, `company.incorporated`, `company.mca_profile` |

### Infrastructure & Security Collectors

| # | Collector | Data Source | What It Checks | Signal Types |
|---|-----------|-------------|---------------|-------------|
| 22 | `rdap_whois` | RDAP protocol | Domain registration, registrant, creation date | `domain.whois` |
| 23 | `ipinfo_geo` | IPinfo API (free tier) | Server location, ASN, ISP | `server.geo` |
| 24 | `shodan_internetdb` | Shodan free census | Open ports, services | `server.ports` |
| 25 | `urlscan_search` | urlscan.io (free tier) | Website tech/security scan | `security.scan` |
| 26 | `common_crawl` | Common Crawl archives | Historical website snapshots | `archive.snapshot` |

### Specialized Collectors

| # | Collector | Data Source | Purpose |
|---|-----------|-------------|---------|
| 27 | `open_food_facts` | Open Food Facts DB | Product/nutrition data for F&B brands |
| 28 | `meta_creative_vision` | Vision analysis | Ad creative quality assessment |

### Data Source Summary

```
FREE SERP (SearXNG)
├── Google, Bing, DuckDuckGo, Wikipedia, Brave, Mojeek, Qwant
├── IP rotation via CloudProxy
└── Engine auto-blocking on rate limits

PUBLIC APIs (No Key Required)
├── Meta Ad Library
├── RDAP (domain whois)
├── Shodan InternetDB
├── Common Crawl
└── Certificate Transparency logs

FREE TIER APIs (Key Required, $0 Cost)
├── Google PageSpeed Insights (25K req/day)
├── YouTube Data API
└── IPinfo (50K req/month)

SERP-BASED RESEARCH (via SearXNG)
├── Crunchbase funding profiles
├── Tracxn company/funding data
├── Tofler.in MCA filing data (Indian companies)
└── General news, Wikipedia, business directories
```

---

## 5. Research Pipeline — The 5-Node Intelligence Graph

When a brand qualifies for deep research, the `DossierWF` workflow triggers a **LangGraph supervisor** — a sequential 5-node state machine that builds a comprehensive intelligence dossier.

### Pre-Research: Enrichment Phase

Before the graph runs, `DossierWF` executes 9 enrichment collectors to ensure fresh signal data:

```
tech_stack → pagespeed → social_presence → content_blog →
ads_txt → email_maturity → crunchbase_funding → tracxn_funding → tofler_company
```

All signals are stored in PostgreSQL. The graph then fetches these signals and uses them as **ground truth**.

### Node Architecture

```
                    Signal Data (from 29 collectors)
                              │
                              ▼
              ┌───────────────────────────────┐
              │       1. RESEARCH NODE        │
              │   (Company Profile Builder)    │
              │   Model: CHEAP tier            │
              │   Tools: Website, SERP, Wapp   │
              └───────────────┬───────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │      2. COMPETITOR NODE       │
              │   (Market Landscape Mapper)    │
              │   Model: CHEAP tier            │
              │   Tools: SERP                  │
              └───────────────┬───────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │     3. OPPORTUNITY NODE       │
              │   (Gap & Service Recommender)  │
              │   Model: FAST tier             │
              │   Tools: News                  │
              └───────────────┬───────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │        4. SCORE NODE          │
              │   (Lead Qualification Engine)  │
              │   Model: SMART tier            │
              │   Uses: All prior node outputs │
              └───────────────┬───────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │      5. OUTREACH NODE         │
              │   (Sequence Generator)         │
              │   Model: SMART tier            │
              │   Gate: Score ≥ 40 to proceed  │
              └───────────────────────────────┘
```

### Node 1: Research (Brand Profile)

**Purpose:** Build a structured company profile from web evidence and collector signals.

**Data Sources Used:**
- Homepage content (fetched via httpx)
- Tech stack detection (Wappalyzer-style pattern matching)
- 4 targeted SearXNG queries:
  1. Company info (revenue, employees, funding, HQ)
  2. Brand/product positioning
  3. Funding search (Tracxn, Crunchbase, Inc42, Entrackr)
  4. Funding news coverage
- Pre-computed `digital_footprint` from collector signals
- Funding rounds from Crunchbase, Tracxn, and Tofler collectors

**Output Fields:**

| Section | Fields |
|---------|--------|
| **Company** | legal_name, brand_name, domain, hq, geos, employees_est, revenue_band, funding_stage, revenue_estimate_inr, founded_year, founders, CIN, authorized_capital, paid_up_capital |
| **Positioning** | category, sub_category, audience, audience_segments, price_band, USP_summary |
| **Digital Footprint** | channels_active, martech_stack, web_perf_signal, seo_signal, paid_signal, social_signal, email_signal, content_maturity, programmatic_signal |
| **Funding History** | Array of rounds: round_type, amount, amount_usd, date, investors, source, valuation |

**Key Constraint:** Revenue, employees, and funding are extracted ONLY from evidence. If no data found, fields are set to `null` — never estimated or hallucinated.

### Node 2: Competitor (Market Landscape)

**Purpose:** Identify 3–5 direct competitors and map service gaps.

**Data Sources Used:**
- SERP search for competitors in same category
- Normalized collector signals (injected into prompt for competitive positioning)

**Output:** Competitor list with positioning, strengths, threat level. Gap map across 10 service areas with evidence-backed rationale.

**Constraint:** Only competitors explicitly named in SERP evidence. Focus on Indian market.

### Node 3: Opportunity (Diagnosis & Recommendations)

**Purpose:** Diagnose brand weaknesses and recommend specific services.

**Output:** 1–2 sentence diagnosis, top 3 weaknesses with evidence, services recommended with estimated impact, urgency factors, estimated deal size in INR.

### Node 4: Score (Lead Qualification)

**Purpose:** Assign a quantified score (0–100) and tier. Detailed in [Section 6](#6-lead-qualification--scoring--tiering).

### Node 5: Outreach (Email Sequence)

**Purpose:** Generate a personalized 5-touch cold email sequence, LinkedIn InMail, and voicemail script.

**Gate:** If score < 40, outreach is skipped entirely.

**Output:** 5 subject lines (3–5 words each), 5 email bodies (<75 words each), LinkedIn InMail (50 words), voicemail script (20 seconds). Each email must cite at least one specific metric from the score breakdown.

---

## 6. Lead Qualification — Scoring & Tiering

### Scoring Dimensions

The score node evaluates each brand across **9 weighted dimensions** that sum to 1.0:

| Dimension | Weight | What It Measures | High Signal (0.7+) |
|-----------|--------|-----------------|-------------------|
| **Budget Potential** | 0.18 | Can they afford our services? | Explicit revenue/funding evidence |
| **Marketing Maturity Gap** | 0.15 | Do they NEED our services? | No SEO, no paid ads, weak website |
| **Growth Stage Fit** | 0.12 | Are they in our sweet spot? | Series A–C, 50–500 employees, 2–8 years old |
| **Competitive Pressure** | 0.12 | Are competitors outperforming? | 3+ service gaps in gap map |
| **Channel Weakness Severity** | 0.12 | How bad are their weak channels? | No SEO + no paid ads combined |
| **Urgency** | 0.10 | Is there a trigger to act NOW? | Recent funding, hiring marketing roles |
| **Brand Fit** | 0.08 | Would our services help them? | D2C, consumer-facing, digital-first |
| **Expansion Likelihood** | 0.08 | Are they entering new markets? | Evidence of new geos/categories |
| **Decision Maker Reach** | 0.05 | Can we reach the buyer? | Indian company, identifiable founders/CMO |

Each dimension is scored 0.0–1.0, multiplied by weight, and summed to produce a final score of 0–100.

### Tier Assignment

| Tier | Score Range | Meaning | Action |
|------|------------|---------|--------|
| **Hot** | 85–100 | High-budget brand with clear gaps, active trigger, strong fit | Immediate approval + outreach |
| **Warm** | 60–84 | Good-fit brand with gaps, moderate budget signal | Approval queue → outreach |
| **Watchlist** | 35–59 | Right category but thin data, unclear budget | Monitor for changes |
| **Park** | 0–34 | Wrong fit, no gaps, too early, or B2B | Archive, no outreach |

### Calibration Anchors

The score prompt includes explicit calibration anchors to ensure consistency:

- **Hot (85+):** Funded D2C, weak digital presence, actively hiring marketing, 3+ competitor gaps, clear urgency trigger.
- **Warm (60–84):** Good-fit brand, some measurable gaps, moderate budget signal, missing urgency or unclear financials.
- **Watchlist (35–59):** Right category, thin data, unclear budget, 1–2 weak channels only.
- **Park (<35):** Wrong category, no digital gaps, too early stage, B2B/non-digital brand.

### Per-Service Opportunity Scoring

In addition to the overall score, each of GrabOn's 10 services is scored independently (0–100):

| Service | Score 90+ | Score 50–70 | Score <30 |
|---------|----------|------------|----------|
| **SEO** | No SEO signal detected | Some gaps, basic presence | Strong organic traffic |
| **Performance Marketing** | No paid ads but has budget | Basic/inconsistent ads | Sophisticated multi-channel |
| **Content** | No blog or stale (>6 months) | Basic, infrequent posts | Active content engine |
| **Social Media** | <3 platforms present | Present but inconsistent | Strong engagement |
| **Email Marketing** | No ESP or DMARC | Has ESP, weak automation | Mature with segmentation |
| **Web Design** | PageSpeed <50/100 | Decent but dated (50–70) | Modern, fast (80+) |
| **Influencer** | No program detected | Basic, few mentions | Running campaigns |
| **Affiliate** | No program detected | Single-network, basic | Multi-network, active |
| **Programmatic** | No ads.txt found | Basic partners (<5) | Active network (10+) |
| **ASO** | App rating <4.0 | Basic listing | Optimized, high ratings |

### Score Output

```json
{
  "total": 66,
  "tier": "warm",
  "breakdown": {
    "budget_potential": { "score": 0.3, "evidence": "Revenue <$10M, Series A funding" },
    "marketing_maturity_gap": { "score": 0.7, "evidence": "No blog, no paid ads, PageSpeed 47" },
    ...
  },
  "service_gaps": {
    "seo": { "score": 90, "evidence": "No SEO signal detected", "priority": "high" },
    "content": { "score": 90, "evidence": "No blog found", "priority": "high" },
    ...
  },
  "why": [
    "Significant gaps in content and performance marketing",
    "Poor PageSpeed score (47/100) indicates web design need",
    "No SEO presence despite D2C category"
  ],
  "confidence": 0.7,
  "estimated_deal_value_inr": 5000000,
  "predicted_conversion_probability": 0.5,
  "top_services": ["content", "performance_marketing", "web_design"]
}
```

---

## 7. Pre-Qualification Gate

Before investing in a full dossier (which costs LLM tokens across 5 nodes), the system runs a **lightweight pre-qualification check** during the discovery phase.

### How It Works

1. `AutoDiscoveryWF` discovers new brands via collectors
2. Before fanning out to `DossierWF`, calls `pre_qualify_brands_activity`
3. For each brand, `compute_quick_score()` checks existing signals in DB:

| Signal Type | Points |
|------------|--------|
| Funding found | +20 |
| Revenue signal | +15 |
| Hiring signal | +10 |
| Paid ads detected | +10 |
| Tech stack depth ≥ 5 | +5 |
| Social presence | +5 |
| SEO signal | +5 |
| PageSpeed data | +3 |

4. **Pass threshold: ≥ 25 points**
5. Brands below threshold are logged and skipped — saving full dossier cost (~$0.03–0.05 per brand)

### Impact

Without pre-qualification, every discovered brand would trigger a full 5-node research pipeline. With the gate:
- ~40–60% of discovered brands are filtered out early
- Cost savings: ~$0.05 per filtered brand × hundreds of brands per week
- Quality improvement: full research budget is concentrated on higher-potential leads

---

## 8. Approval System — Human-in-the-Loop

### Purpose

No outreach is sent without human review. The approval system ensures that Hot and Warm tier leads are verified by a human before any emails are dispatched.

### Flow

```
DossierWF completes
       │
       ▼
  Score tier = hot or warm?
       │
  YES  │  NO
       │   └──→ No approval needed (watchlist/park)
       ▼
  Create approval record
  (status: "pending")
       │
       ▼
  Appears in approval queue
  (dashboard + API)
       │
       ▼
  Reviewer decides:
  ├── APPROVED → Create outreach sequence
  ├── REJECTED → Mark as park, no outreach
  └── EDITED   → Apply corrections, re-research
```

### Approval Record

| Field | Description |
|-------|-------------|
| entity_type | Always "dossier" |
| entity_id | Dossier ID being reviewed |
| brand_id | Brand being evaluated |
| status | pending → approved / rejected |
| notes | Auto-generated: "Auto-flagged: {tier} tier lead" |
| reviewer | Email of approving user |
| decided_at | Timestamp of decision |

---

## 9. Outreach Engine

### Email Sequence Structure

When a lead is approved, a 5-touch cold email sequence is created:

| Touch | Day | Angle | Max Words |
|-------|-----|-------|-----------|
| 1 | Day 0 | Trigger/pain hook — specific observation about their brand | 75 |
| 2 | Day 3 | Case study — peer company, one metric | 50 |
| 3 | Day 7 | New angle — different pain or persona | 60 |
| 4 | Day 10 | Pattern interrupt — question, contrarian take | 40 |
| 5 | Day 14 | Breakup — graceful close, referral trigger | 30 |

### Personalization Requirements

Each email must:
- Open with the prospect, never with "I" or "My name is"
- Cite at least one factual data point from the score/research
- Have exactly one CTA per email (specific ask, not generic)
- Subject line: 3–5 words, lowercase acceptable

### Channels Generated

- **Email sequence** (5 touches)
- **LinkedIn InMail** (50 words, ends with question)
- **Voicemail script** (20 seconds, plain text)

### Dispatch

The `OutreachSequenceWF` polls every 15 minutes for due sequences and sends via SMTP. Reply tracking via IMAP.

---

## 10. Monitoring & Change Detection

### Weekly Re-Research

The `MonitoringWF` runs on a schedule (configurable, default weekly) to re-analyze tracked brands:

1. Fetch brands not researched in N days (default: 7)
2. For each brand:
   - Re-run all 9 enrichment collectors (fresh signals)
   - Re-run full 5-node LangGraph pipeline
   - Compare new score vs. previous score
   - If drift > threshold (default: 10 points) → emit alert
3. Score drifts tracked for trend analysis

### Change Types Detected

| Change | Detection Method | Action |
|--------|-----------------|--------|
| **Score increase** | New score > old score + threshold | May upgrade tier (park → warm) |
| **Score decrease** | New score < old score - threshold | May downgrade tier |
| **New funding** | Crunchbase/Tracxn/news signals | Urgency trigger re-evaluation |
| **Hiring signals** | New LinkedIn job postings | Growth indicator |
| **Competitor entry** | New competitors in SERP | Competitive pressure update |
| **Tech stack changes** | New platforms/tools detected | Service gap changes |

---

## 11. LLM Stack & Cost Control

### Model Tiers

| Tier | Default Model | Cost (per 1M tokens) | Used For |
|------|--------------|---------------------|---------|
| **CHEAP** | NVIDIA NIM llama-3.3-70b | $0 in / $0 out | Research node, competitor node |
| **FAST** | OpenAI gpt-4o-mini | $0.15 in / $0.60 out | Opportunity node |
| **SMART** | OpenAI gpt-4o | $2.50 in / $10 out | Score node, outreach node |
| **FALLBACK** | NVIDIA NIM llama-3.1-70b | $0 in / $0 out | Safety net if others fail |

### Tier Fallback Chain

```
SMART → FAST → CHEAP → FALLBACK
```

If a model fails (rate limit, error, unavailable), the system automatically falls back to the next tier.

### Budget Gate (Atomic Cost Control)

Every LLM call goes through a **pre-debit budget gate**:

1. **Estimate** max cost based on input tokens + max_output_tokens
2. **Atomic SQL**: `UPDATE budget SET spent_cents = spent_cents + :estimate WHERE spent_cents + :estimate <= cap_cents`
3. If budget exceeded → `BudgetGateError` (non-retryable, blocks call)
4. After call completes → **Refund** difference between estimate and actual cost
5. Daily cap: configurable (default $5 USD / 500 cents)

### Cost Per Lead

| Stage | Estimated Cost |
|-------|---------------|
| Discovery (per brand found) | ~$0.00 (free collectors) |
| Pre-qualification check | ~$0.01 (signal lookup only) |
| Full dossier (5 nodes) | ~$0.03–0.10 (depends on model tier) |
| Total per qualified lead | ~$0.05–0.15 |

---

## 12. Frontend Dashboard

### Pages

| Page | Purpose |
|------|---------|
| **Dashboard** | KPI cards (leads by tier, CPQL, budget usage), pipeline funnel, top leads, signal activity |
| **Brand Detail** | Full dossier view — company, positioning, funding, competitors, opportunity, score breakdown, outreach sequence |
| **Approvals** | Human-in-the-loop queue — pending approvals with dossier preview, approve/reject actions |
| **Compare** | Side-by-side brand comparison across all dimensions |
| **Agent** | Real-time workflow monitoring (SSE-streamed node progress) |

### Signal Visualization

The brand detail page groups collectors into categories:

| Category | Collectors Shown |
|----------|-----------------|
| **Web & Tech** | tech_stack, pagespeed |
| **Social** | social_presence, youtube_social |
| **Marketing** | google_ads_transparency, meta_ad_library, content_blog, email_maturity, ads_txt, affiliate_program |
| **SEO** | seo_rank_tracking |
| **Apps** | app_store, ios_app_store |
| **Funding Intelligence** | crunchbase_funding, tracxn_funding, tofler_company |

### Real-Time Updates

- **SSE streams** for workflow progress (node-by-node)
- **Live notifications** for new approvals, score drifts, workflow completions

---

## 13. Data Flow — End to End

```
┌─────────────────────────────────────────────────────────────────────┐
│                    AUTONOMOUS DISCOVERY (every 6h)                   │
│                                                                     │
│  ICP Seed Queries ──→ 6 Discovery Collectors ──→ New Brand Signals  │
│                                                                     │
│  Signals stored in PostgreSQL (deduplicated by dedupe_key)          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    PRE-QUALIFICATION GATE                            │
│                                                                     │
│  Check signal score (funding +20, revenue +15, hiring +10, ...)     │
│  Pass threshold: ≥ 25 points                                       │
│  SKIP brands below threshold                                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ (qualified brands only)
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    DOSSIER WORKFLOW                                  │
│                                                                     │
│  1. Resolve brand domain/name                                       │
│  2. Run 9 enrichment collectors (tech, social, funding, MCA, etc.)  │
│  3. Run 5-node LangGraph supervisor:                                │
│     research → competitor → opportunity → score → outreach          │
│  4. Persist dossier (versioned) + trace                             │
│  5. If tier = hot/warm → create approval                            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
              tier = hot/warm       tier = watchlist/park
                    │                     │
                    ▼                     ▼
           ┌───────────────┐      ┌──────────────┐
           │ APPROVAL QUEUE│      │  MONITORING   │
           │ (Human Review)│      │  (Weekly Re-  │
           │               │      │   research)   │
           └───────┬───────┘      └──────────────┘
                   │
            ┌──────┴──────┐
         Approved      Rejected
            │              │
            ▼              ▼
    ┌───────────────┐   (Park)
    │ OUTREACH      │
    │ 5-touch email │
    │ + LinkedIn    │
    │ + Voicemail   │
    └───────────────┘
```

---

## 14. Database Schema

### Core Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| **brands** | Brand registry | id, name, domain, root_domain, status, created_at |
| **signals** | Collector findings | brand_id, source, type, value_num, value_text, payload (JSONB), dedupe_key (unique), observed_at |
| **dossiers** | Research output | brand_id, version, data (JSONB), markdown, embedding (vector 384-dim), cost_cents, generated_at |
| **approvals** | HITL review queue | entity_type, entity_id, brand_id, status, reviewer, notes, decided_at |
| **agent_traces** | Workflow audit log | workflow_id, brand_id, agent, steps (JSONB), total_cost_cents, duration_ms |
| **budget** | Daily LLM spend | day (PK), spent_cents, cap_cents |
| **brand_competitors** | Competitive relationships | brand_id, competitor_id, source, confidence |
| **outreach_sequences** | Email campaigns | brand_id, to_email, subjects, bodies, schedule, current_step, status, next_send_at |
| **brand_blocklist** | Veto rules | kind (block/allow/existing_client), pattern, scope (domain/name), reason |
| **collector_runs** | Collector audit | collector, params, signals_emitted, signals_deduped, status |

### Signal Deduplication

Every signal has a `dedupe_key` — a string combining source, brand identifier, and observation window (typically date). PostgreSQL's unique constraint prevents duplicate storage. Collectors can be re-run safely at any frequency.

---

## 15. Deployment & Infrastructure

### Local Development

```bash
docker compose up -d          # Start all services
docker compose build worker   # Rebuild after code changes
docker compose logs worker    # Monitor workflow execution
```

### Service Ports

| Service | Internal Port | External Port |
|---------|--------------|---------------|
| PostgreSQL | 5432 | 5433 |
| Temporal | 7233 | 7233 |
| Temporal UI | 8080 | 8081 |
| SearXNG | 8080 | 8888 |
| FastAPI | 8000 | 8002 |
| Next.js | 3000 | 3001 |

### Scaling Considerations

| Component | Scaling Strategy |
|-----------|-----------------|
| **Workers** | Horizontal — add more Temporal worker replicas |
| **SearXNG** | Horizontal + IP rotation pools (CloudProxy) |
| **PostgreSQL** | Vertical + read replicas for analytics |
| **API** | Horizontal behind load balancer |
| **Frontend** | CDN (Vercel or equivalent) |

### Resilience

- **Temporal**: Durable workflows survive worker restarts, automatic retries per activity
- **LLM Tier Fallback**: If primary model fails, falls back through tier chain
- **Collector Graceful Degradation**: Individual collector failures don't block the pipeline
- **Budget Gate**: Non-blocking — logs warning and proceeds if gate check fails
- **SearXNG Engine Blocking**: Auto-disables blocked engines, rotates to available ones

---

## Appendix A: Veto System (Blocklist)

The brand blocklist prevents specific brands from entering the pipeline:

| Rule Type | Effect |
|-----------|--------|
| **block** | Brand is completely excluded from discovery, scoring, and outreach |
| **existing_client** | Brand is flagged for CSM team — still researched, no new outbound |
| **allow** | Whitelist mode — if any allow rules exist, only matching brands pass |

Matching supports exact domain, glob patterns (`*.example.com`), and substring on brand name.

---

## Appendix B: Cost Tracking & CPQL

**CPQL (Cost Per Qualified Lead)** is tracked in the analytics dashboard:

```
CPQL = Total LLM Spend / Number of Hot+Warm Leads Generated
```

Per-node cost is tracked via `NodeRecord`:
- `model`: Which LLM model was used
- `in_tokens`: Input token count
- `out_tokens`: Output token count
- `cost_cents`: Computed cost in cents

Total workflow cost is the sum across all nodes + trace persistence.

---

*This document describes GrabOn Intel as of May 2026. The system is under active development — collector count, scoring dimensions, and LLM model selection are subject to change.*
