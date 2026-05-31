"""Node implementations for the research supervisor.

Each node:
  - reads what it needs from `state`
  - may call tools via `ToolRegistry`
  - calls an LLM via `complete()` with a tightly scoped prompt
  - appends a NodeRecord to `state["nodes"]`
  - returns a partial state dict (LangGraph merges it back)

Prompts deliberately request JSON output for downstream consumption.
LLM JSON parsing is forgiving: malformed output is captured under `_raw`.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import get_settings
from ..llm import Tier, complete
from ..logging import get_logger
from .state import NodeRecord, ResearchState

log = get_logger(__name__)

_TIER_MAP = {"cheap": Tier.CHEAP, "fast": Tier.FAST, "smart": Tier.SMART}


def _get_services() -> list[str]:
    return get_settings().parsed_services()


_SERVICES = [
    "performance_marketing",
    "seo",
    "social_media",
    "email_marketing",
    "influencer",
    "content",
    "affiliate",
    "programmatic",
    "web_design",
    "aso",
    "analytics",
]


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        # Try to extract the first balanced JSON object.
        start = text.find("{")
        end = text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                pass
        return {"_raw": text[:4000]}


# Fields stripped from signal payloads (noise, not diagnostic)
_PAYLOAD_STRIP = {
    "domain", "ip", "hostnames", "scan_id", "nameservers",
    "mx_providers", "spf_includes", "search_terms",
}


_AUTHORITATIVE_COLLECTORS = {
    "email_maturity", "ads_txt", "pagespeed", "tech_stack",
    "content_blog", "rdap_whois", "ipinfo_geo", "shodan_internetdb",
}
_SCRAPER_COLLECTORS = {
    "social_presence", "seo_rank_tracking", "google_ads_transparency",
    "meta_ad_library", "linkedin_jobs", "youtube_social",
    "affiliate_program",
}


def _signal_data_quality(source: str, findings: list[dict]) -> str:
    """Tag signal quality: confirmed_present / confirmed_absent / unverified."""
    has_real_data = any(
        f.get("value_num") or (f.get("value_text") and f["value_text"] not in ("no_ads_txt", "no_affiliate", "none", "non_d2c"))
        for f in findings
    )
    if has_real_data:
        return "confirmed_present"
    if source in _AUTHORITATIVE_COLLECTORS:
        return "confirmed_absent"
    return "unverified"


def _normalize_signal_data(raw: dict | None) -> dict[str, list[dict]] | None:
    """Strip noise from signal payloads, tag with data quality."""
    if not raw:
        return None
    out: dict[str, list[dict]] = {}
    for source, findings in raw.items():
        if not isinstance(findings, list):
            continue
        clean_findings = []
        quality = _signal_data_quality(source, findings)
        for f in findings:
            entry: dict[str, Any] = {
                "type": f.get("type"),
                "value_num": f.get("value_num"),
                "value_text": f.get("value_text"),
                "data_quality": quality,
            }
            payload = f.get("payload")
            if isinstance(payload, dict):
                clean_payload = {
                    k: v for k, v in payload.items()
                    if k not in _PAYLOAD_STRIP and v is not None
                }
                if clean_payload:
                    entry["payload"] = clean_payload
            clean_findings.append(entry)
        if clean_findings:
            out[source] = clean_findings
    return out


def _compute_digital_footprint(signal_data: dict | None) -> dict[str, Any]:
    """Build digital_footprint deterministically from collector signals."""
    fp: dict[str, Any] = {
        "channels_active": [],
        "martech_stack": [],
        "web_perf_signal": "unknown",
        "web_perf_quality": "unverified",
        "seo_signal": "unknown",
        "seo_quality": "unverified",
        "paid_signal": "none",
        "paid_quality": "unverified",
        "social_signal": "unknown",
        "social_quality": "unverified",
        "email_signal": "unknown",
        "email_quality": "unverified",
        "content_maturity": "unknown",
        "content_quality": "unverified",
        "programmatic_signal": "none",
        "programmatic_quality": "unverified",
    }
    if not signal_data:
        return fp

    def _payload(source: str) -> dict:
        findings = signal_data.get(source, [])
        if findings and isinstance(findings, list) and findings[0].get("payload"):
            return findings[0]["payload"]
        return {}

    # PageSpeed → web_perf_signal (authoritative)
    ps = _payload("pagespeed")
    perf = ps.get("performance_score")
    if perf is not None:
        fp["web_perf_quality"] = "confirmed_present"
        if perf >= 80:
            fp["web_perf_signal"] = f"strong ({perf}/100)"
        elif perf >= 50:
            fp["web_perf_signal"] = f"moderate ({perf}/100)"
        else:
            fp["web_perf_signal"] = f"weak ({perf}/100)"

    # SEO rank tracking → seo_signal (scraper — unverified if empty)
    seo = _payload("seo_rank_tracking")
    kw_found = seo.get("keywords_found", 0)
    avg_rank = seo.get("avg_rank")
    if kw_found and avg_rank:
        fp["seo_quality"] = "confirmed_present"
        if kw_found >= 5 and avg_rank <= 10:
            fp["seo_signal"] = f"strong ({kw_found} keywords, avg rank {avg_rank})"
        elif kw_found >= 2:
            fp["seo_signal"] = f"moderate ({kw_found} keywords, avg rank {avg_rank})"
        else:
            fp["seo_signal"] = f"basic ({kw_found} keywords, avg rank {avg_rank})"
    elif kw_found:
        fp["seo_quality"] = "confirmed_present"
        fp["seo_signal"] = f"basic ({kw_found} keywords)"

    # Social presence → social_signal (scraper — unverified if empty)
    social = _payload("social_presence")
    plat_count = social.get("platform_count", 0)
    social_score = social.get("social_score")
    platforms = social.get("platforms_active", [])
    if plat_count:
        fp["social_quality"] = "confirmed_present"
        if plat_count >= 4 or (social_score and social_score >= 70):
            fp["social_signal"] = f"strong ({plat_count} platforms)"
        elif plat_count >= 2:
            fp["social_signal"] = f"moderate ({plat_count} platforms: {', '.join(platforms)})"
        else:
            fp["social_signal"] = f"basic ({plat_count} platform: {', '.join(platforms)})"
        fp["channels_active"].extend(platforms)

    # Email maturity → email_signal (authoritative DNS check)
    email = _payload("email_maturity")
    maturity = email.get("maturity_level")
    providers = email.get("email_providers", [])
    if maturity:
        fp["email_quality"] = "confirmed_present" if maturity != "none" else "confirmed_absent"
        checks = [email.get("has_spf"), email.get("has_dkim"), email.get("has_dmarc")]
        passed = sum(1 for c in checks if c)
        provider_str = f" via {providers[0]}" if providers else ""
        fp["email_signal"] = f"{maturity} ({passed}/3 auth checks{provider_str})"

    # Google Ads + Meta Ads → paid_signal (scraper — unverified if empty)
    gads = _payload("google_ads_transparency")
    meta = _payload("meta_ad_library")
    g_count = gads.get("appearances", 0)
    m_count = meta.get("active_ads_count", 0)
    total_ads = g_count + m_count
    if total_ads >= 10:
        fp["paid_quality"] = "confirmed_present"
        fp["paid_signal"] = f"strong ({total_ads} ads detected)"
    elif total_ads >= 3:
        fp["paid_quality"] = "confirmed_present"
        fp["paid_signal"] = f"moderate ({total_ads} ads detected)"
    elif total_ads > 0:
        fp["paid_quality"] = "confirmed_present"
        fp["paid_signal"] = f"basic ({total_ads} ads detected)"

    # Content blog → content_maturity (authoritative HTTP check)
    blog = _payload("content_blog")
    if blog.get("has_blog"):
        fp["content_quality"] = "confirmed_present"
        post_count = blog.get("post_count_estimate", 0)
        mat = blog.get("maturity", "basic")
        fp["content_maturity"] = f"{mat} ({post_count} posts)"
        if "blog" not in fp["channels_active"]:
            fp["channels_active"].append("blog")
    elif blog:
        fp["content_quality"] = "confirmed_absent"
        fp["content_maturity"] = "none"

    # Ads.txt → programmatic_signal (authoritative HTTP check)
    adstxt = _payload("ads_txt")
    if adstxt.get("has_ads_txt"):
        fp["programmatic_quality"] = "confirmed_present"
        partners = adstxt.get("partners_count", 0)
        fp["programmatic_signal"] = f"active ({partners} partners)" if partners else "basic"
    elif adstxt:
        fp["programmatic_quality"] = "confirmed_absent"
        fp["programmatic_signal"] = "none (no ads.txt)"

    # Tech stack → martech_stack
    tech = _payload("tech_stack")
    techs = tech.get("all_technologies", [])
    if techs:
        fp["martech_stack"] = techs
    analytics = tech.get("analytics", [])
    if analytics:
        fp["channels_active"].extend([a for a in ["google_analytics", "facebook_pixel"] if any(a.replace("_", " ") in t.lower() for t in analytics)])

    # YouTube → channel
    yt = _payload("youtube_social")
    if yt.get("subscribers") or yt.get("videos"):
        fp["channels_active"].append("youtube")

    # App stores
    app = _payload("app_store") or _payload("ios_app_store")
    if app.get("rating") or app.get("reviews_count"):
        fp["channels_active"].append("app")

    # Crunchbase / Tracxn / Tofler funding signals
    funding_rounds_found: list[dict] = []
    for src in ("crunchbase_funding", "tracxn_funding", "tofler_company"):
        findings = signal_data.get(src, [])
        if not isinstance(findings, list):
            continue
        for f in findings:
            sig_type = f.get("type", "")
            payload = f.get("payload") or {}
            if sig_type in ("funding_round", "funding.round"):
                funding_rounds_found.append({
                    "source": src,
                    "round": payload.get("round_type") or payload.get("round") or f.get("value_text"),
                    "amount": payload.get("amount"),
                    "date": payload.get("date"),
                    "investors": payload.get("investors", []),
                })
            elif sig_type in ("total_funding", "funding.total"):
                fp["total_funding_signal"] = f.get("value_text") or str(f.get("value_num"))
            elif sig_type in ("revenue_estimate", "revenue.estimate"):
                fp["revenue_signal"] = f.get("value_text")
            elif sig_type == "company.revenue_filing":
                fp["revenue_filing"] = f.get("value_text")
            elif sig_type == "company.employees":
                fp["employees_signal"] = f.get("value_num") or f.get("value_text")
            elif sig_type == "company.capital":
                fp["capital_signal"] = f.get("value_text")
            elif sig_type == "company.incorporated":
                fp["incorporated_signal"] = f.get("value_text")
            elif sig_type == "company.directors":
                fp["directors_signal"] = payload.get("directors", [])

    if funding_rounds_found:
        fp["funding_rounds_from_collectors"] = funding_rounds_found

    # Vane AI research — PRIMARY source for company intelligence
    vane_signals = signal_data.get("vane_research", [])
    if isinstance(vane_signals, list):
        for vs in vane_signals:
            payload = vs.get("payload") or {}
            query_type = payload.get("query_type", "")
            answer = payload.get("full_answer", "")
            sources = payload.get("sources", [])
            source_urls = [s.get("url", "") for s in sources if s.get("url")]
            if answer:
                if query_type == "company_overview":
                    fp["ai_company_research"] = answer[:3000]
                    fp["ai_company_sources"] = source_urls[:5]
                elif query_type in ("funding_revenue", "recent_activity"):
                    key = "ai_funding_research" if query_type == "funding_revenue" else "ai_recent_activity"
                    fp[key] = answer[:3000]
                    if query_type == "funding_revenue":
                        fp["ai_funding_sources"] = source_urls[:5]
                elif query_type == "digital_presence":
                    fp["ai_digital_presence_research"] = answer[:3000]
                    fp["ai_digital_sources"] = source_urls[:5]
                elif query_type == "competitive_landscape":
                    fp["ai_competitive_research"] = answer[:3000]

    # Vane digital presence can override unverified collector signals
    ai_digital = fp.get("ai_digital_presence_research", "")
    if ai_digital:
        lower_digital = ai_digital.lower()
        # Social: check if Vane found social accounts
        social_keywords = ("instagram", "youtube", "facebook", "twitter", "linkedin", "followers", "subscriber")
        if any(kw in lower_digital for kw in social_keywords) and fp["social_quality"] == "unverified":
            fp["social_quality"] = "confirmed_present"
            fp["social_signal"] = "verified by AI research (see ai_digital_presence_research)"
        # Paid ads: check if Vane found ad activity
        paid_keywords = (
            "paid ads", "paid advertising", "google ads", "meta ads",
            "running ads", "ad spend", "ad campaign", "roas",
            "facebook ads", "instagram ads", "video ads",
            "advertising on meta", "advertising on google",
        )
        if any(kw in lower_digital for kw in paid_keywords) and fp["paid_quality"] == "unverified":
            fp["paid_quality"] = "confirmed_present"
            fp["paid_signal"] = "verified by AI research (see ai_digital_presence_research)"
        # Blog: check if Vane found blog
        blog_keywords = (
            "active blog", "maintains a blog", "blog that",
            "operate a blog", "maintain a blog", "blog as part",
            "content strategy", "blog to maintain",
        )
        if any(kw in lower_digital for kw in blog_keywords) and fp["content_quality"] == "unverified":
            fp["content_quality"] = "confirmed_present"
            fp["content_maturity"] = "verified by AI research (see ai_digital_presence_research)"

    fp["channels_active"] = list(dict.fromkeys(fp["channels_active"]))
    return fp


def _record(node: str, llm_res, tools_used: list[str], error: str | None = None) -> NodeRecord:
    if llm_res is None:
        return {
            "node": node,
            "model": None,
            "in_tokens": 0,
            "out_tokens": 0,
            "cost_cents": 0,
            "tools_used": tools_used,
            "preview": "",
            "error": error,
        }
    return {
        "node": node,
        "model": llm_res.model_id,
        "in_tokens": llm_res.input_tokens,
        "out_tokens": llm_res.output_tokens,
        "cost_cents": llm_res.cost_cents,
        "tools_used": tools_used,
        "preview": llm_res.content[:240],
        "error": error,
    }


def _emit(rec: NodeRecord) -> dict[str, Any]:
    """Return a partial state dict carrying one new node record + its cost.

    LangGraph merges this back via the Annotated `operator.add` reducers
    declared on `ResearchState.nodes` and `ResearchState.total_cost_cents`.
    """
    return {"nodes": [rec], "total_cost_cents": int(rec.get("cost_cents", 0))}


# --- node factories — each returns an async fn closed over a tool registry ---


def make_research_node(tools):
    async def research_node(state: ResearchState) -> dict:
        domain = state.get("brand_domain")
        name = state.get("brand_name") or domain or f"brand_{state.get('brand_id')}"
        tools_used: list[str] = []
        tool_payload: dict[str, Any] = {}

        if domain:
            r = await tools.website.safe(url=domain)
            tools_used.append("website_fetch")
            tool_payload["website"] = r.data
            state["website"] = r.data

        wap = await tools.wappalyzer.safe(domain=domain) if domain else None
        if wap and not wap.degraded:
            tools_used.append("wappalyzer")
            tool_payload["wappalyzer"] = wap.data
            state["wappalyzer"] = wap.data

        # Compute digital_footprint deterministically from signals
        signal_data = state.get("signal_data")
        computed_footprint = _compute_digital_footprint(signal_data)
        normalized_signals = _normalize_signal_data(signal_data)

        # Build AI research section SEPARATELY (primary source, shown first)
        ai_research_section = ""
        _AI_KEYS = [
            ("ai_company_research", "COMPANY PROFILE"),
            ("ai_funding_research", "FUNDING & REVENUE"),
            ("ai_digital_presence_research", "DIGITAL PRESENCE"),
            ("ai_competitive_research", "COMPETITIVE LANDSCAPE"),
        ]
        for key, label in _AI_KEYS:
            val = computed_footprint.get(key)
            if val:
                ai_research_section += f"\n### {label}:\n{val[:2500]}\n"

        # Also keep in tool_payload for backward compat
        for key, _ in _AI_KEYS:
            if computed_footprint.get(key):
                tool_payload[key] = computed_footprint[key]

        # Recent activity from Vane Query 2
        vane_signals = signal_data.get("vane_research", []) if signal_data else []
        if isinstance(vane_signals, list):
            for vs in vane_signals:
                payload = vs.get("payload") or {}
                if payload.get("query_type") == "recent_activity":
                    answer = payload.get("full_answer", "")
                    if answer:
                        ai_research_section += f"\n### RECENT ACTIVITY:\n{answer[:2000]}\n"
                        break

        signal_section = ""
        if normalized_signals:
            signal_section = (
                "\n\nCOLLECTOR SIGNALS (factual data from automated scanners — "
                "use these as ground truth for digital_footprint):\n"
                f"{json.dumps(normalized_signals, default=str)[:10000]}"
            )

        prompt = (
            "You are a senior brand analyst. Build a structured profile from the "
            "evidence below.\n\n"
            "CRITICAL: Every value you output MUST come from explicit evidence. "
            "If no source states a fact, set that field to null. NEVER guess, infer, "
            "or estimate. Do NOT add '(inferred)' to any value.\n\n"
            "RULES:\n"
            "1. Extract facts from evidence text — website content, SERP snippets, "
            "Wappalyzer data, AI research. Look carefully at ALL sources.\n"
            "2. AI RESEARCH (ai_company_research, ai_funding_research, "
            "ai_digital_presence_research, ai_competitive_research) — these are the PRIMARY "
            "source for company data. They contain AI-synthesized answers with cited "
            "web sources. Extract: HQ city, founding year, founders, employee count, "
            "revenue, funding rounds, social media accounts, ad activity. "
            "These fields often ONLY exist in AI research.\n"
            "3. For revenue: LOOK CAREFULLY in ai_company_research and ai_funding_research "
            "for revenue/ARR/net revenue figures. Common formats: '₹200 crore', '$24M ARR', "
            "'revenue of ₹X crore'. Extract as revenue_band (e.g. '₹100-500 crore') and "
            "revenue_estimate_inr (numeric INR value). Use ONLY figures stated. Null if absent.\n"
            "4. For employees: use ONLY figures stated in evidence. Null if absent.\n"
            "5. For hq: use ONLY city/location stated in evidence. Null if absent.\n"
            "6. For founded_year: use ONLY year stated in evidence. Null if absent.\n"
            "7. For positioning: extract from website content + AI research.\n"
            "8. For digital_footprint: use pre-computed collector data EXACTLY. "
            "Only add channels_active or martech_stack from evidence collectors missed.\n"
            "9. FUNDING: extract ALL rounds from AI research. Include amount, "
            "investors, date for each round. Empty array if none found.\n\n"
            "Output strict JSON:\n"
            "company {legal_name, brand_name, domain, hq, geos[], employees_est, "
            "revenue_band, funding_stage, revenue_estimate_inr, "
            "founded_year, public_listed (true/false/null), "
            "founders[], authorized_capital, paid_up_capital, cin},\n"
            "positioning {category, sub_category, audience, "
            "audience_segments[], price_band, USP_summary},\n"
            "digital_footprint: USE THE PRE-COMPUTED VALUES BELOW as the base, "
            "only add to channels_active/martech_stack if AI research evidence shows more.\n"
            "funding_history: array of ALL funding rounds found in the evidence. "
            "For EACH round include:\n"
            "  {round_type (seed/pre-seed/Series A/B/C/D/Pre-IPO/IPO/debt/grant), "
            "  amount (exact figure as stated, e.g. '$2.6M' or '₹50 crore'), "
            "  amount_usd (converted to USD if possible, null if not), "
            "  date (YYYY-MM-DD if exact date found, YYYY-MM if only month, YYYY if only year), "
            "  investors: [list of investor names exactly as stated in evidence], "
            "  source (which AI research citation or URL this came from), "
            "  valuation (post-money valuation if mentioned, null if not)}.\n"
            "CRITICAL funding rules:\n"
            "- ONLY include rounds explicitly mentioned in the evidence. Never fabricate.\n"
            "- If a snippet says 'raised $X from Y in Z' — extract amount=$X, investors=[Y], date=Z.\n"
            "- Include ALL rounds found, not just the latest. Order chronologically.\n"
            "- If no funding info found, set funding_history to empty array [].\n"
            "- Copy amounts EXACTLY as stated — do not convert or round.\n\n"
            f"Brand: {name}\nDomain: {domain}\n\n"
            f"AI RESEARCH FINDINGS (PRIMARY SOURCE — extract facts from here first):\n"
            f"{ai_research_section}\n\n"
            f"PRE-COMPUTED digital_footprint (from real scanners — use as-is):\n"
            f"{json.dumps(computed_footprint)}\n\n"
            f"Evidence:\n{json.dumps(tool_payload)[:16000]}"
            f"{signal_section}"
        )
        res = await complete(tier=Tier.SMART, prompt=prompt, json_mode=True, max_output_tokens=4096)
        out = _try_json(res.content)

        # Retry with broader search if critical fields are empty
        company = out.get("company") if isinstance(out, dict) else None
        positioning = out.get("positioning") if isinstance(out, dict) else None
        funding = out.get("funding_history") if isinstance(out, dict) else None
        _COMPANY_FIELDS = ("hq", "employees_est", "revenue_band", "founded_year", "founders", "funding_stage", "revenue_estimate_inr")
        _POSITION_FIELDS = ("category", "audience", "USP_summary", "price_band", "sub_category")
        missing_company = not company or sum(1 for k in _COMPANY_FIELDS if company.get(k)) < 3
        missing_positioning = not positioning or sum(1 for k in _POSITION_FIELDS if positioning.get(k)) < 2
        missing_funding = not funding and ai_research_section and any(
            kw in ai_research_section.lower() for kw in ("funding", "raised", "series", "seed", "investment")
        )

        if missing_company or missing_positioning or missing_funding:
            log.info("research.retry_missing_fields", brand=name, missing_company=missing_company, missing_positioning=missing_positioning, missing_funding=missing_funding)
            missing_fields = []
            if missing_company:
                missing_fields.append("company (hq, employees_est, revenue_band, founded_year, founders, funding_stage, revenue_estimate_inr, authorized_capital, cin)")
            if missing_positioning:
                missing_fields.append("positioning (category, sub_category, audience, audience_segments, price_band, USP_summary)")
            if missing_funding:
                missing_fields.append("funding_history (extract ALL rounds from evidence — round_type, amount, date, investors)")

            retry_prompt = (
                "The first research pass missed critical fields. Re-read ALL the "
                "evidence more carefully — especially the AI RESEARCH section below.\n\n"
                f"MISSING: {', '.join(missing_fields)}\n\n"
                "RULES:\n"
                "- ONLY extract values explicitly stated in the evidence.\n"
                "- If a field has NO evidence, set it to null. NEVER guess or infer.\n"
                "- Do NOT add '(inferred)' suffix — either the data is factual or null.\n"
                "- For digital_footprint: use pre-computed collector values as-is.\n"
                "- For funding_history: extract EVERY round mentioned — round_type, amount, "
                "date, investors, source. Do not skip rounds.\n"
                "- For company: extract founders as a list of full names, CIN if mentioned, "
                "authorized_capital and paid_up_capital if available.\n\n"
                f"Brand: {name}\nDomain: {domain}\n\n"
                f"AI RESEARCH (PRIMARY SOURCE — read this first):\n"
                f"{ai_research_section}\n\n"
                f"Original research: {json.dumps(out)[:3000]}\n"
                f"All evidence (re-read carefully):\n"
                f"{json.dumps(tool_payload)[:8000]}"
                f"{signal_section}\n\n"
                "Output the COMPLETE updated JSON with same schema as before "
                "(company, positioning, digital_footprint, funding_history). "
                "Merge new findings into the original — do not discard existing data."
            )
            res2 = await complete(tier=Tier.SMART, prompt=retry_prompt, json_mode=True, max_output_tokens=4096)
            retry_out = _try_json(res2.content)
            if isinstance(retry_out, dict) and not retry_out.get("_raw"):
                for section in ("company", "positioning", "digital_footprint"):
                    orig_section = out.get(section, {}) or {}
                    retry_section = retry_out.get(section, {}) or {}
                    for k, v in retry_section.items():
                        if v is not None and not orig_section.get(k):
                            orig_section[k] = v
                    out[section] = orig_section
                retry_funding = retry_out.get("funding_history", [])
                orig_funding = out.get("funding_history", [])
                if retry_funding and len(retry_funding) > len(orig_funding or []):
                    out["funding_history"] = retry_funding
                elif not orig_funding and retry_funding:
                    out["funding_history"] = retry_funding
                res = res2
                log.info("research.retry_success", brand=name)

        return {
            "research": out,
            "website": state.get("website"),
            "wappalyzer": state.get("wappalyzer"),
            **_emit(_record("research", res, tools_used)),
        }

    return research_node


_INDIA_D2C_COMPETITORS: dict[str, list[dict[str, str]]] = {
    "skincare": [
        {"name": "Minimalist", "domain": "beminimalist.co"},
        {"name": "Dot & Key", "domain": "dotandkey.com"},
        {"name": "Plum Goodness", "domain": "plumgoodness.com"},
        {"name": "mCaffeine", "domain": "mcaffeine.com"},
        {"name": "Mamaearth", "domain": "mamaearth.in"},
        {"name": "The Derma Co", "domain": "thedermaco.com"},
    ],
    "beauty": [
        {"name": "Sugar Cosmetics", "domain": "sugarcosmetics.com"},
        {"name": "Nykaa", "domain": "nykaa.com"},
        {"name": "MyGlamm", "domain": "myglamm.com"},
        {"name": "Colorbar", "domain": "colorbarcosmetics.com"},
        {"name": "Faces Canada", "domain": "facescanada.com"},
    ],
    "fashion": [
        {"name": "Bewakoof", "domain": "bewakoof.com"},
        {"name": "The Souled Store", "domain": "thesouledstore.com"},
        {"name": "Snitch", "domain": "snitch.co.in"},
        {"name": "Bonkers Corner", "domain": "bonkerscorner.com"},
        {"name": "NOBERO", "domain": "nobero.com"},
    ],
    "health": [
        {"name": "Wellbeing Nutrition", "domain": "wellbeingnutrition.com"},
        {"name": "Oziva", "domain": "oziva.in"},
        {"name": "Kapiva", "domain": "kapiva.in"},
        {"name": "Man Matters", "domain": "manmatters.com"},
        {"name": "Boldfit", "domain": "boldfit.in"},
    ],
    "food": [
        {"name": "Slurrp Farm", "domain": "slurrpfarm.com"},
        {"name": "True Elements", "domain": "trueelements.com"},
        {"name": "Yogabar", "domain": "yogabars.in"},
        {"name": "Whole Truth", "domain": "thewholetruthfoods.com"},
        {"name": "Open Secret", "domain": "opensecret.in"},
    ],
}


def _get_category_competitors(category: str, brand_domain: str | None) -> list[dict[str, str]]:
    """Return pre-seeded competitors for an Indian D2C category."""
    cat = (category or "").lower()
    for key, comps in _INDIA_D2C_COMPETITORS.items():
        if key in cat or cat in key:
            return [c for c in comps if c["domain"] != brand_domain][:5]
    for key in ("cosmetic", "makeup"):
        if key in cat:
            return [c for c in _INDIA_D2C_COMPETITORS["beauty"] if c["domain"] != brand_domain][:5]
    for key in ("wellness", "supplement", "nutrition", "fitness"):
        if key in cat:
            return [c for c in _INDIA_D2C_COMPETITORS["health"] if c["domain"] != brand_domain][:5]
    for key in ("snack", "beverage", "organic"):
        if key in cat:
            return [c for c in _INDIA_D2C_COMPETITORS["food"] if c["domain"] != brand_domain][:5]
    for key in ("apparel", "clothing", "wear"):
        if key in cat:
            return [c for c in _INDIA_D2C_COMPETITORS["fashion"] if c["domain"] != brand_domain][:5]
    return []


def make_competitor_node(tools):
    async def competitor_node(state: ResearchState) -> dict:
        tools_used: list[str] = []
        research = state.get("research") or {}
        category = (research.get("positioning") or {}).get("category") or ""
        name = state.get("brand_name") or state.get("brand_domain") or f"brand_{state.get('brand_id')}"
        brand_domain = state.get("brand_domain")

        # Use Vane AI competitive research + category fallback (no direct SearXNG)
        fallback_section = ""
        fallback_comps = _get_category_competitors(category, brand_domain)
        if fallback_comps:
            fallback_section = (
                "\n\nKnown Indian D2C competitors for this category "
                "(verify relevance before including):\n"
                f"{json.dumps(fallback_comps)}\n"
            )
            tools_used.append("category_fallback")

        normalized_signals = _normalize_signal_data(state.get("signal_data"))
        signal_section = ""
        if normalized_signals:
            signal_section = (
                f"\n\nCollector Signals (factual data — use to assess competitive positioning):\n"
                f"{json.dumps(normalized_signals, default=str)[:3000]}"
            )

        # Extract Vane competitive research from signal data
        vane_competitive = ""
        vane_signals = (state.get("signal_data") or {}).get("vane_research", [])
        if isinstance(vane_signals, list):
            for vs in vane_signals:
                payload = vs.get("payload") or {}
                if payload.get("query_type") == "competitive_landscape":
                    vane_competitive = payload.get("full_answer", "")[:3000]
                    tools_used.append("vane_research")
                    break

        prompt = (
            "Identify 3-5 REAL competitors to this brand. CRITICAL RULES:\n"
            "- Use AI competitive research below as PRIMARY source for competitor names.\n"
            "- Use the fallback competitor list to fill gaps if AI research is thin.\n"
            "- Competitors must be in the SAME market/geography — focus on INDIAN market.\n"
            "  Indian D2C brands compete with Indian D2C brands, not global conglomerates.\n"
            "- If brand HQ is in India, ALL competitors must also operate in India.\n"
            "- NEVER list a company from a completely different industry.\n\n"
            "For each competitor provide:\n"
            "- name, domain\n"
            "- positioning: how they compete\n"
            "- strengths: what they do well\n"
            "- threat_level: high/medium/low\n\n"
            "Use the COLLECTOR SIGNALS below to assess competitive gaps:\n"
            "- Compare tech stack maturity, ad spend presence, SEO signals, social reach\n"
            "- If brand has low PageSpeed scores, that's a web performance gap\n"
            "- If brand has no paid ads but competitors do, that's a performance marketing gap\n"
            "- If brand's email auth (SPF/DKIM/DMARC) is weak, that's an email marketing gap\n\n"
            "Output a gap_map mapping each gap where the brand trails competitors "
            "to one of these Grabon services: "
            f"{_SERVICES}. Only include gaps supported by evidence.\n\n"
            f"Brand summary: {json.dumps(research)[:2500]}\n"
            f"AI Competitive Research (from Vane):\n{vane_competitive}\n"
            f"{fallback_section}"
            f"{signal_section}\n\n"
            "Return strict JSON: {competitors:[{name, domain, positioning, strengths, "
            "threat_level}], gap_map:{service: rationale}}"
        )
        res = await complete(tier=Tier.CHEAP, prompt=prompt, json_mode=True, max_output_tokens=800)
        out = _try_json(res.content)
        return {"competitor": out, **_emit(_record("competitor", res, tools_used))}

    return competitor_node


def make_opportunity_node(tools):
    async def opportunity_node(state: ResearchState) -> dict:
        tools_used: list[str] = []
        name = state.get("brand_name") or state.get("brand_domain") or f"brand_{state.get('brand_id')}"

        news = await tools.news.safe(query=f"{name} funding hiring growth")
        if not news.degraded:
            tools_used.append("google_news")
            state["news"] = news.data

        normalized_signals = _normalize_signal_data(state.get("signal_data"))
        signal_section = ""
        if normalized_signals:
            signal_section = (
                f"\nCollector Signals (factual — cite these):\n"
                f"{json.dumps(normalized_signals, default=str)[:4000]}"
            )

        prompt = (
            "You are Grabon's growth strategist. For the brand below, output "
            "strict JSON: {diagnosis, top_3_weaknesses[3], services_recommended:"
            "[{service, rationale, estimated_impact, confidence}], "
            "urgency_factors[], estimated_deal_size_inr}. Use ONLY services from "
            f"{_SERVICES}.\n\n"
            "CRITICAL: Base your diagnosis on COLLECTOR SIGNALS below.\n"
            "Each signal has a data_quality tag:\n"
            "- confirmed_present: real data found — cite specific metric.\n"
            "- confirmed_absent: authoritative check confirmed absence — real gap.\n"
            "- unverified: scraper found nothing — does NOT mean absent. Say 'unverified'.\n"
            "Check ai_digital_presence_research for Vane-verified social/ads data "
            "before claiming a channel is weak.\n"
            "Do NOT make vague claims without data backing.\n\n"
            f"Research: {json.dumps(state.get('research'))[:2500]}\n"
            f"Competitors: {json.dumps(state.get('competitor'))[:2500]}\n"
            f"News: {json.dumps(news.data)[:1500]}"
            f"{signal_section}"
        )
        res = await complete(tier=Tier.FAST, prompt=prompt, json_mode=True, max_output_tokens=800)
        out = _try_json(res.content)
        return {
            "opportunity": out,
            "news": news.data if not news.degraded else None,
            **_emit(_record("opportunity", res, tools_used)),
        }

    return opportunity_node


def make_score_node(_tools):
    async def score_node(state: ResearchState) -> dict:
        settings = get_settings()
        score_tier = _TIER_MAP.get(settings.score_llm_tier, Tier.SMART)
        services = settings.parsed_services()

        # DIGITAL FOOTPRINT injected FIRST so LLM reads quality tags before scoring
        research_data = state.get("research") or {}
        dfp = research_data.get("digital_footprint") if isinstance(research_data, dict) else None
        dfp_section = ""
        if dfp:
            dfp_section = (
                "DIGITAL FOOTPRINT (pre-computed from collectors — AUTHORITATIVE quality tags):\n"
                f"{json.dumps(dfp)[:3000]}\n\n"
                "MANDATORY QUALITY RULES (read BEFORE scoring):\n"
                "- IF social_quality=confirmed_present → social_media score MUST be <40.\n"
                "- IF paid_quality=confirmed_present → performance_marketing score MUST be <40.\n"
                "- IF content_quality=confirmed_present → content score MUST be <40.\n"
                "- IF email_quality=confirmed_present → email_marketing score MUST be <40.\n"
                "- IF programmatic_quality=confirmed_present → programmatic score MUST be <40.\n"
                "- unverified quality → score 40-60, NEVER 80+.\n"
                "- confirmed_absent → score 70-90 (real gap, real opportunity).\n\n"
            )

        prompt = (
            f"{dfp_section}"
            "You are a lead scoring analyst for Grabon, a digital marketing agency "
            "targeting Indian D2C/ecommerce brands. Score this lead 0-100.\n\n"
            "WEIGHTED FACTORS (must sum to 1.0):\n"
            "- budget_potential (0.12): Do they have money? Look for: revenue figures, "
            "funding raised, employee count >50, premium pricing. Score HIGH (0.7-1.0) "
            "only if explicit revenue/funding evidence exists. Score LOW (0.1-0.3) if "
            "no financial data found.\n"
            "- growth_stage_fit (0.12): Are they at the right stage? Ideal: Series A-C, "
            "50-500 employees, 2-8 years old. Score LOW for pre-revenue or massive "
            "corporates (>5000 employees).\n"
            "- marketing_maturity_gap (0.15): Do they NEED us? Look at: basic website, "
            "no SEO signal, weak social, no paid ads, basic martech. Score HIGH if "
            "digital presence is clearly weak. Score LOW if already sophisticated.\n"
            "- competitive_pressure (0.12): Are competitors outperforming them digitally? "
            "Score HIGH if gap_map has 3+ service gaps vs competitors.\n"
            "- urgency (0.10): Is there a trigger? Funding round, hiring marketing, "
            "competitor launch, seasonal peak, rapid growth, recent PR. Score 0.5-0.7 "
            "if reasonable urgency can be INFERRED (e.g. competitive pressure + weak "
            "channels). Score 0.8+ with explicit trigger.\n"
            "- brand_fit (0.08): Would Grabon's services (performance marketing, SEO, "
            "social, content) actually help? Score LOW for B2B SaaS or non-digital.\n"
            "- expansion_likelihood (0.12): Signs of growth: new markets, new categories, "
            "international expansion, hiring, new product lines. Score 0.5-0.6 if the "
            "company is in a growth stage (Series A-D, growing headcount) even without "
            "explicit expansion news. Score 0.7+ with direct evidence.\n"
            "- channel_weakness_severity (0.15): How bad are their weak channels? "
            "No SEO + no paid = HIGH. One weak channel = MEDIUM.\n"
            "- decision_maker_reach (0.04): Can we reach them? Indian company, "
            "identifiable founders/CMO = HIGH. Unknown leadership = LOW.\n\n"
            "CALIBRATION ANCHORS (use these to set your scale):\n"
            "- Score 70-100 (hot): Funded D2C brand with clear marketing gaps, good "
            "budget signal, and 3+ competitor gaps. Does NOT require a perfect urgency "
            "trigger — strong fit + gaps + budget is enough.\n"
            "- Score 50-69 (warm): Good-fit brand, some gaps, moderate budget signal, "
            "but missing urgency or unclear financials.\n"
            "- Score 30-49 (watchlist): Right category but thin data, unclear budget, "
            "or only 1-2 weak channels.\n"
            "- Score 0-29 (park): Wrong fit, no gaps, too early stage, or B2B/non-digital.\n\n"
            "CRITICAL RULES:\n"
            "- Brands with thin data should score 40-60.\n"
            "- Brands with STRONG evidence (funding + multiple gaps + budget signal) "
            "SHOULD score 70+. Do not artificially cap scores.\n"
            "- Each factor score MUST cite specific evidence or say 'no evidence'.\n"
            "- The total MUST equal the weighted sum (not an arbitrary number).\n"
            "- Differentiate aggressively. A brand with funding+gaps+urgency ≠ a brand "
            "with just a website.\n\n"
            "TIER ASSIGNMENT (from total):\n"
            "- hot: total >= 70 (strong evidence across most factors)\n"
            "- warm: 50 <= total < 70\n"
            "- watchlist: 30 <= total < 50\n"
            "- park: total < 30\n\n"
            "PER-SERVICE OPPORTUNITY SCORING:\n"
            f"For each of Grabon's services ({services}), score the OPPORTUNITY 0-100.\n\n"
            "DATA QUALITY RULES (CRITICAL — read this first):\n"
            "Each signal has a data_quality tag:\n"
            "- confirmed_present: collector verified the data exists. Use as-is.\n"
            "- confirmed_absent: authoritative check (DNS, HTTP endpoint) confirmed "
            "absence. This IS evidence of a gap — score appropriately.\n"
            "- unverified: collector scraped but found nothing. This means WE COULD NOT "
            "VERIFY — NOT that the brand lacks it. Score 40-60 and flag as 'unverified'.\n"
            "- If Vane AI research (ai_digital_presence_research) explicitly confirms "
            "presence with source URLs, that OVERRIDES an unverified collector finding. "
            "Use the Vane finding.\n"
            "- If Vane AI research explicitly says 'Not found' with citations checked, "
            "treat as confirmed_absent.\n"
            "- NEVER score 80+ on unverified data alone. Only score 80+ if "
            "confirmed_absent OR Vane explicitly confirms absence.\n"
            "- ALWAYS CHECK THE DIGITAL FOOTPRINT SECTION BELOW. It has pre-computed "
            "quality tags (social_quality, paid_quality, etc.) that are AUTHORITATIVE. "
            "If social_quality=confirmed_present, DO NOT score social_media as a gap. "
            "If paid_quality=confirmed_present, DO NOT score performance_marketing as a gap.\n\n"
            "SERVICE SCORING:\n"
            "- affiliate: 80+ only if confirmed no affiliate program. Unverified=50.\n"
            "- influencer: 80+ only if Vane confirms no influencer/creator activity. Unverified=50.\n"
            "- content: 80+ if blog confirmed_absent or dead (>6mo stale). Unverified=50.\n"
            "- seo: 80+ if SEO collector confirmed low rank AND Vane confirms weak organic. Unverified=50.\n"
            "- social_media: 80+ ONLY if Vane confirms <2 platforms with follower data. "
            "If collector says unverified, check ai_digital_presence_research first. Unverified=40.\n"
            "- performance_marketing: 80+ ONLY if confirmed no ads AND Vane confirms no "
            "paid campaigns. Unverified=50.\n"
            "- email_marketing: use email_maturity collector (authoritative DNS check). "
            "80+ if confirmed basic/none.\n"
            "- web_design: use pagespeed collector (authoritative). 80+ if score <50.\n"
            "- aso: 80+ if app exists with low rating. 0 if no app.\n"
            "- programmatic: use ads_txt collector (authoritative). 80+ if confirmed absent.\n\n"
            "Return strict JSON: {total:int, breakdown:{factor:{score:float 0-1, "
            "evidence:str}}, service_gaps:{service_name:{score:int 0-100, "
            "evidence:str, priority:str(high/medium/low)}},"
            "why:[3 bullets citing specific evidence], red_flags:[], "
            "confidence:float 0-1, confidence_explanation:str, "
            "predicted_conversion_probability:float 0-1, estimated_deal_value_inr:int, "
            "tier:str, top_services:[top 3 service names by opportunity score]}.\n\n"
            "CRITICAL: For service_gaps evidence, cite ACTUAL collector findings:\n"
            "- Quote specific metrics: follower counts, PageSpeed scores, blog dates, etc.\n"
            "- Include data_quality tag: '(confirmed_absent)', '(confirmed_present)', '(unverified)'\n"
            "- For unverified gaps, evidence MUST say 'unverified — collector could not confirm'\n"
            "- For confirmed gaps, cite the exact collector value\n"
            "- Check ai_digital_presence_research for social/ads/blog verification\n"
            "- NEVER say 'no evidence found' — say what was checked and result\n\n"
            f"Research: {json.dumps(state.get('research'))[:2000]}\n"
            f"Competitors: {json.dumps(state.get('competitor'))[:2000]}\n"
            f"Opportunity: {json.dumps(state.get('opportunity'))[:2500]}\n"
        )

        prompt += (
            f"\nSignal Evidence (from collectors — factual data, cite specific metrics):\n"
            f"{json.dumps(_normalize_signal_data(state.get('signal_data')), default=str)[:6000]}"
        )
        res = await complete(tier=score_tier, prompt=prompt, json_mode=True, max_output_tokens=1200)
        out = _try_json(res.content)
        return {"score": out, **_emit(_record("score", res, []))}

    return score_node


def make_outreach_node(_tools):
    async def outreach_node(state: ResearchState) -> dict:
        outreach_tier = _TIER_MAP.get(get_settings().outreach_llm_tier, Tier.FAST)
        sc = state.get("score") or {}
        total = sc.get("total")
        if isinstance(total, (int, float)) and total < 40:
            log.info("outreach.skipped_low_score", score=total)
            return {
                "outreach": {"skipped": True, "reason": "score<40"},
                **_emit(_record("outreach", None, [], error="skipped_low_score")),
            }

        opp = state.get("opportunity") or {}
        service_gaps = sc.get("service_gaps", {})
        top_weaknesses = opp.get("top_3_weaknesses", [])
        diagnosis = opp.get("diagnosis", "")

        evidence_section = ""
        if service_gaps:
            gap_lines = []
            for svc, gap in service_gaps.items():
                if isinstance(gap, dict) and gap.get("evidence"):
                    ev_text = gap['evidence']
                    priority = gap.get('priority', '?')
                    quality = "confirmed" if "confirmed" in ev_text.lower() else "unverified"
                    gap_lines.append(f"- {svc}: {ev_text} (priority: {priority}, quality: {quality})")
            if gap_lines:
                evidence_section = "\nService Gap Evidence (cite at least one in each email):\n" + "\n".join(gap_lines[:6])

        prompt = (
            "Apply Grabon FORGE-COLD-EMAIL rules. Produce a 5-touch sequence "
            "(day 1, 3, 7, 10, 14). Subject 3-5 words. Body <75 words each. "
            "Open with prospect, not us. No filler.\n\n"
            "CRITICAL: Each email body MUST reference at least one specific metric "
            "or finding from the evidence below. Examples: 'your PageSpeed score of 38', "
            "'your 3 active ad campaigns', 'no DMARC record detected'. "
            "Generic claims without data = useless.\n\n"
            "DATA QUALITY RULES:\n"
            "- Only cite findings tagged (confirmed_present) or (confirmed_absent).\n"
            "- NEVER cite unverified findings as fact. If all you have is unverified, "
            "frame as 'we noticed an opportunity' not 'you don't have X'.\n"
            "- Prefer confirmed_absent gaps — these are real selling points.\n\n"
            "Output strict JSON: "
            "{subjects[5], bodies[5], linkedin_inmail (50 words, ends w/ a "
            "question), voicemail_script (20s, plaintext)}.\n\n"
            f"Diagnosis: {diagnosis[:500]}\n"
            f"Top weaknesses: {json.dumps(top_weaknesses)[:500]}\n"
            f"Score breakdown: {json.dumps(sc.get('breakdown', {}))[:800]}"
            f"{evidence_section}\n"
            f"Opportunity: {json.dumps(opp)[:1500]}"
        )
        res = await complete(tier=outreach_tier, prompt=prompt, json_mode=True, max_output_tokens=1000)
        out = _try_json(res.content)
        return {"outreach": out, **_emit(_record("outreach", res, []))}

    return outreach_node
