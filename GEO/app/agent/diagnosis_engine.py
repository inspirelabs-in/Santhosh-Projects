"""
Diagnosis Engine -- the core product differentiator.
Gathers evidence from brand_mentions + SERP + citations + content,
builds structured LLM prompt, returns specific root causes and action items.

Answers: "WHY does AI rank competitor above you, WHAT to fix."
"""
import asyncio
import hashlib
import json
import logging

from app.agent.content_comparator import build_citation_overlaps, analyze_citations_for_prompt
from app.config import get_settings
from app.database import run_db

log = logging.getLogger("geo.diagnosis")

COST_PER_MILLION = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
}

DIAGNOSIS_PROMPT = """You are an expert AI Search Visibility analyst. Diagnose exactly WHY AI engines prefer certain competitors over the target brand for this keyword.

CRITICAL RULES (violation = invalid output):
1. ONLY state facts that appear in the evidence sections below. NEVER assume, infer, or fabricate data.
2. Every root_cause.evidence field MUST quote or directly reference a specific data point from the evidence (a URL, a rank number, a word count, a schema type, a snippet). If you cannot point to a specific data point, do NOT include that root cause.
3. Every action_item MUST reference a real competitor URL or real metric from the evidence. "Improve content quality" is REJECTED. "Add FAQ schema like competitor coupondinia.in/amazon (which has FAQPage schema, target has none)" is ACCEPTED.
4. If evidence is insufficient to diagnose a cause, say so in the summary. Do NOT fill gaps with generic SEO advice.
5. Do NOT recommend actions for data you have not seen. If no content preview is available for a competitor, do not claim to know what content they have.
6. confidence MUST reflect evidence completeness: 0.9+ only if all 7 evidence sources have real data. Below 0.5 if 3+ sources are empty.

## KEYWORD: {keyword}

## AI ENGINE DATA (parsed brand mentions and rankings)
{ai_evidence}

## RAW AI RESPONSES (actual text AI engines returned for this query)
{raw_ai_evidence}

## COUPON DATA (coupons AI engines mentioned -- valid vs hallucinated)
{coupon_evidence}

## SERP DATA (Google organic rankings for same keyword)
{serp_evidence}

## CITATION OVERLAP (AI-cited URLs that also rank in Google SERP)
{overlap_evidence}

## CONTENT COMPARISON (cached competitor page data from citation_pages)
{content_evidence}

## STRUCTURED GAP ANALYSIS (competitor vs target content comparison)
{comparison_evidence}

## TARGET BRAND: {target_domain}
Target brand current status: {target_status}

## PREVIOUS DIAGNOSIS (from last analysis cycle — use as context, update if evidence changed)
{previous_diagnosis}

## EVIDENCE COMPLETENESS
{evidence_completeness}

---

INSTRUCTIONS FOR RETURNING RESULTS:
- If previous diagnosis exists, compare current evidence against it. Note what CHANGED (new competitors, rank shifts, new citations).
- Keep root causes that still hold. Drop ones contradicted by new evidence. Add new ones found.
- Mark action_items as "recurring" in evidence_basis if they appeared in previous diagnosis and remain unresolved.

Return JSON only:
{{
  "root_causes": [
    {{
      "category": "content_gap|authority_gap|freshness_gap|structural_gap|relevance_gap",
      "description": "what competitor has that target lacks -- cite actual names, URLs, numbers",
      "evidence": "MUST quote the exact data point from above: e.g. 'Competitor coupondinia.in ranked #1 in chatgpt with cited URL coupondinia.in/amazon (word count: 3200 vs target 800)'"
    }}
  ],
  "action_items": [
    {{
      "action": "specific step referencing real competitor data from evidence above",
      "expected_impact": "high|medium|low",
      "effort": "high|medium|low",
      "target_url": "URL to modify or create",
      "evidence_basis": "which evidence data point supports this action"
    }}
  ],
  "priority": "critical|high|medium|low",
  "confidence": 0.0 to 1.0,
  "summary": "one sentence, referencing actual data",
  "data_gaps": ["list any evidence sources that were empty or insufficient"]
}}"""


def _compute_evidence_hash(prompt_id: str, ai_evidence: str, serp_evidence: str) -> str:
    """Hash structured signals (brand+rank+sentiment tuples, SERP domains) — NOT raw text.
    Raw AI response wording drifts between scrapes even when rankings are identical,
    so we hash only the structured data that actually drives diagnosis decisions."""
    lines = []
    for line in ai_evidence.split("\n"):
        line = line.strip()
        if line.startswith("#") and any(c.isalpha() for c in line):
            parts = line.split(" - ")
            lines.append(parts[0].strip())
    for line in serp_evidence.split("\n"):
        line = line.strip()
        if line.startswith("#") and any(c.isdigit() for c in line):
            domain_part = line.split(" - ")[0].strip() if " - " in line else line
            lines.append(domain_part)
    payload = f"{prompt_id}|" + "|".join(sorted(lines))
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


async def evidence_hash_changed(prompt_id: str, engine_name: str, new_hash: str) -> bool:
    """Compare new evidence hash against last active diagnosis. True = needs re-diagnosis."""
    row = await run_db(lambda conn: conn.execute(
        """SELECT evidence_hash FROM diagnoses
           WHERE prompt_id = %s::uuid AND engine_name = %s AND status = 'active'
           ORDER BY created_at DESC LIMIT 1""",
        (prompt_id, engine_name),
    ).fetchone())
    if not row or not row.get("evidence_hash"):
        return True
    return row["evidence_hash"] != new_hash


async def _gather_previous_diagnosis(prompt_id: str, engine_name: str = "all") -> str:
    """Fetch last active diagnosis for this keyword to provide continuity context."""
    row = await run_db(lambda conn: conn.execute(
        """SELECT summary, root_causes, action_items, confidence, created_at::text as diagnosed_at
           FROM diagnoses
           WHERE prompt_id = %s::uuid AND engine_name = %s AND status = 'active'
           ORDER BY created_at DESC LIMIT 1""",
        (prompt_id, engine_name),
    ).fetchone())

    if not row:
        return "No previous diagnosis exists for this keyword."

    causes = row["root_causes"]
    if isinstance(causes, str):
        causes = json.loads(causes)
    actions = row["action_items"]
    if isinstance(actions, str):
        actions = json.loads(actions)

    lines = [f"Last diagnosed: {row['diagnosed_at'][:16]} (confidence: {row['confidence']})"]
    lines.append(f"Summary: {row['summary']}")
    if causes:
        lines.append("Root causes found:")
        for c in causes[:5]:
            lines.append(f"  - [{c.get('category', '?')}] {c.get('description', '')[:150]}")
    if actions:
        lines.append("Action items:")
        for a in actions[:5]:
            lines.append(f"  - {a.get('action', '')[:150]} (impact: {a.get('expected_impact', '?')})")

    return "\n".join(lines)


async def _log_diagnosis_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = COST_PER_MILLION.get(model, {"input": 0, "output": 0})
    cost = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
    if cost <= 0:
        return 0.0
    def _insert(conn):
        conn.execute(
            """INSERT INTO api_costs (provider, model, input_tokens, output_tokens, cost_usd, purpose)
               VALUES (%s, %s, %s, %s, %s, 'diagnosis')""",
            ("openai", model, input_tokens, output_tokens, cost),
        )
        conn.commit()
    try:
        await run_db(_insert)
    except Exception as e:
        log.warning(f"Failed to log diagnosis cost: {e}")
    return cost


async def _gather_ai_evidence(prompt_id: str) -> str:
    """Gather brand mention data from recent AI engine scrapes."""
    rows = await run_db(lambda conn: conn.execute(
        """SELECT el.engine_name, bm.rank_position, bm.brand_name,
                  bm.is_target_brand, bm.sentiment, bm.context_snippet, bm.cited_url
           FROM brand_mentions bm
           JOIN execution_logs el ON bm.log_id = el.id
           WHERE el.prompt_id = %s::uuid
             AND el.captured_at >= NOW() - INTERVAL '7 days'
           ORDER BY el.engine_name, bm.rank_position""",
        (prompt_id,),
    ).fetchall())

    if not rows:
        return "No AI engine data available for this keyword."

    lines = []
    current_engine = ""
    for r in rows:
        eng = r["engine_name"]
        if eng != current_engine:
            lines.append(f"\n### {eng}")
            current_engine = eng
        target_tag = " [TARGET]" if r["is_target_brand"] else ""
        cited = f" (cited: {r['cited_url']})" if r["cited_url"] else ""
        lines.append(
            f"  #{r['rank_position']} {r['brand_name']}{target_tag} - "
            f"Sentiment: {r['sentiment']}{cited}"
        )
        if r["context_snippet"]:
            lines.append(f"    Context: {r['context_snippet'][:200]}")

    return "\n".join(lines)


async def _gather_raw_ai_responses(prompt_id: str) -> str:
    """Pull actual raw AI engine response text -- the richest evidence source we have.
    Contains the full AI reasoning, competitor details, pricing, freshness signals."""
    rows = await run_db(lambda conn: conn.execute(
        """SELECT engine_name, raw_response_text, captured_at::text as captured_at
           FROM execution_logs
           WHERE prompt_id = %s::uuid
             AND captured_at >= NOW() - INTERVAL '7 days'
             AND raw_response_text IS NOT NULL
             AND LENGTH(raw_response_text) > 50
           ORDER BY captured_at DESC""",
        (prompt_id,),
    ).fetchall())

    if not rows:
        return "No raw AI responses available."

    seen_engines = set()
    lines = []
    for r in rows:
        eng = r["engine_name"]
        if eng in seen_engines:
            continue
        seen_engines.add(eng)
        text = r["raw_response_text"]
        truncated = text[:1500] if len(text) > 1500 else text
        lines.append(f"\n### {eng} (scraped {r['captured_at'][:16]})")
        lines.append(truncated)
        if len(text) > 1500:
            lines.append(f"[...truncated, full response: {len(text)} chars]")

    return "\n".join(lines)


async def _gather_serp_evidence(prompt_id: str) -> str:
    """Gather latest SERP organic rankings."""
    rows = await run_db(lambda conn: conn.execute(
        """SELECT soe.rank_position, soe.title, soe.url, soe.domain, soe.is_target
           FROM serp_organic_entries soe
           JOIN serp_results sr ON soe.serp_id = sr.id
           WHERE sr.prompt_id = %s::uuid
             AND sr.captured_at = (
               SELECT MAX(captured_at) FROM serp_results WHERE prompt_id = %s::uuid
             )
           ORDER BY soe.rank_position
           LIMIT 15""",
        (prompt_id, prompt_id),
    ).fetchall())

    if not rows:
        return "No SERP data available. Google organic rankings not yet crawled."

    lines = ["Google Organic Rankings (latest):"]
    for r in rows:
        target_tag = " [TARGET]" if r["is_target"] else ""
        lines.append(f"  #{r['rank_position']} {r['domain']}{target_tag} - {r['title'][:80]}")
        lines.append(f"    URL: {r['url']}")

    return "\n".join(lines)


async def _gather_overlap_evidence(prompt_id: str) -> str:
    """Gather citation-SERP overlap data."""
    rows = await run_db(lambda conn: conn.execute(
        """SELECT cso.cited_url, cso.engine_name, cso.serp_rank
           FROM citation_serp_overlaps cso
           WHERE cso.prompt_id = %s::uuid
             AND cso.detected_at >= NOW() - INTERVAL '7 days'
           ORDER BY cso.serp_rank NULLS LAST""",
        (prompt_id,),
    ).fetchall())

    if not rows:
        return "No citation-SERP overlaps detected."

    lines = ["URLs cited by AI that also rank in Google SERP:"]
    for r in rows:
        serp_info = f"Google rank #{r['serp_rank']}" if r["serp_rank"] else "Not in Google top results"
        lines.append(f"  {r['cited_url']} - Cited by {r['engine_name']} - {serp_info}")

    return "\n".join(lines)


async def _gather_content_evidence(prompt_id: str, keyword: str) -> str:
    """Pull already-cached citation page data from DB. No live fetching -- uses only data we have."""
    settings = get_settings()
    target_domain = settings.target_domain

    cited_urls = await run_db(lambda conn: conn.execute(
        """SELECT DISTINCT bm.cited_url
           FROM brand_mentions bm
           JOIN execution_logs el ON bm.log_id = el.id
           WHERE el.prompt_id = %s::uuid
             AND bm.cited_url IS NOT NULL AND bm.cited_url != ''
             AND el.captured_at >= NOW() - INTERVAL '7 days'
           LIMIT 5""",
        (prompt_id,),
    ).fetchall())

    target_url_row = await run_db(lambda conn: conn.execute(
        """SELECT DISTINCT soe.url
           FROM serp_organic_entries soe
           JOIN serp_results sr ON soe.serp_id = sr.id
           WHERE sr.prompt_id = %s::uuid AND soe.domain ILIKE %s
           LIMIT 1""",
        (prompt_id, f"%{target_domain}%"),
    ).fetchone())

    all_urls = [row["cited_url"] for row in cited_urls]
    if target_url_row:
        all_urls.append(target_url_row["url"])

    cache_map = {}
    if all_urls:
        placeholders = ",".join(["%s"] * len(all_urls))
        cached_rows = await run_db(lambda conn: conn.execute(
            f"""SELECT url, title, word_count, schema_types, h1_tags, main_text_preview, fetch_status
                FROM citation_pages WHERE url IN ({placeholders}) AND fetch_status = 200""",
            all_urls,
        ).fetchall())
        cache_map = {r["url"]: r for r in cached_rows}

    def _format_page(label, url, cached):
        block = [f"\n{label}: {url}"]
        if cached:
            block.append(f"  Title: {cached['title'] or 'unknown'}")
            block.append(f"  Word count: {cached['word_count'] or 0}")
            schemas = cached["schema_types"] or []
            block.append(f"  Schema: {', '.join(schemas) or 'none'}")
            h1s = cached["h1_tags"] or []
            block.append(f"  H1: {', '.join(h1s[:3]) or 'none'}")
            preview = cached["main_text_preview"] or ""
            if preview:
                block.append(f"  Content preview: {preview[:500]}")
        else:
            block.append("  (content not yet fetched)")
        return block

    lines = []

    if target_url_row:
        lines.extend(_format_page("TARGET PAGE", target_url_row["url"], cache_map.get(target_url_row["url"])))
    else:
        lines.append(f"\nTARGET: {target_domain} has no page ranking for this keyword in Google SERP.")

    for row in cited_urls:
        url = row["cited_url"]
        if target_domain.lower() in url.lower():
            continue
        lines.extend(_format_page("COMPETITOR PAGE", url, cache_map.get(url)))

    if not lines:
        return "No content data available. No citation pages cached yet."

    return "\n".join(lines)


async def _gather_coupon_evidence(prompt_id: str) -> str:
    """Pull coupon codes mentioned by AI engines from DB."""
    rows = await run_db(lambda conn: conn.execute(
        """SELECT hc.coupon_code, hc.associated_merchant, el.engine_name,
                  COUNT(*) OVER (PARTITION BY hc.coupon_code) as mention_count
           FROM ai_hallucinated_coupons hc
           JOIN execution_logs el ON hc.log_id = el.id
           WHERE el.prompt_id = %s::uuid
             AND el.captured_at >= NOW() - INTERVAL '7 days'
           ORDER BY el.engine_name, hc.coupon_code""",
        (prompt_id,),
    ).fetchall())

    if not rows:
        return "No coupon data available for this keyword."

    lines = ["Coupon codes AI engines mentioned for this query:"]
    for r in rows:
        freq = f"({r['mention_count']}x)" if r["mention_count"] > 1 else ""
        lines.append(f"  [{r['engine_name']}] {r['coupon_code']} for {r['associated_merchant']} {freq}")

    unique_codes = len({r["coupon_code"] for r in rows})
    unique_merchants = len({r["associated_merchant"] for r in rows})
    lines.append(f"\nSummary: {unique_codes} unique codes for {unique_merchants} brands across {len(rows)} mentions")

    return "\n".join(lines)


async def _gather_comparison_evidence(prompt_id: str, keyword: str) -> str:
    """Run LLM content comparator on top cited competitor pages vs target."""
    try:
        comparisons = await analyze_citations_for_prompt(prompt_id, keyword)
    except Exception as e:
        log.warning(f"Content comparison failed: {e}")
        return "Content comparison unavailable."

    if not comparisons:
        return "No competitor pages available for structured comparison."

    lines = []
    for i, c in enumerate(comparisons[:5], 1):
        lines.append(f"\n### Competitor {i}: {c.competitor_url}")
        lines.append(f"  Engine: {c.engine_name} | Confidence: {c.confidence:.0%}")
        if c.content_gaps:
            lines.append(f"  Content gaps: {'; '.join(c.content_gaps[:4])}")
        if c.structural_advantages:
            lines.append(f"  Structural advantages: {'; '.join(c.structural_advantages[:3])}")
        if c.authority_signals:
            lines.append(f"  Authority signals: {'; '.join(c.authority_signals[:3])}")
        if c.recommendations:
            lines.append(f"  Recommendations: {'; '.join(c.recommendations[:3])}")

    return "\n".join(lines)


def _determine_target_status(ai_evidence: str, target_domain: str) -> str:
    lower = ai_evidence.lower()
    if "[target]" not in lower:
        return "ABSENT - target brand not mentioned in any AI engine response"

    target_lines = [l for l in ai_evidence.split("\n") if "[TARGET]" in l]
    if not target_lines:
        return "ABSENT"

    positions = []
    sentiments = []
    for line in target_lines:
        if "#" in line:
            try:
                pos = int(line.split("#")[1].split()[0])
                positions.append(pos)
            except (ValueError, IndexError):
                pass
        if "Negative" in line:
            sentiments.append("Negative")
        elif "Positive" in line:
            sentiments.append("Positive")
        else:
            sentiments.append("Neutral")

    avg_pos = sum(positions) / len(positions) if positions else 0
    neg_count = sentiments.count("Negative")

    if avg_pos <= 2 and neg_count == 0:
        return f"GOOD - avg rank #{avg_pos:.1f}, mostly positive/neutral sentiment"
    elif avg_pos <= 3:
        return f"MODERATE - avg rank #{avg_pos:.1f}, {neg_count} negative mentions"
    else:
        return f"WEAK - avg rank #{avg_pos:.1f}, often ranked below competitors, {neg_count} negative mentions"


def _validate_diagnosis_output(result: dict, sources_with_data: int, evidence_sources: dict) -> dict:
    """Post-validate LLM output: reject ungrounded claims, cap confidence by data completeness."""
    all_evidence_text = " ".join(evidence_sources.values()).lower()

    validated_causes = []
    for rc in result.get("root_causes", []):
        evidence_field = rc.get("evidence", "")
        if not evidence_field or len(evidence_field) < 10:
            log.debug(f"Dropping root cause with no evidence: {rc.get('description', '')[:60]}")
            continue
        validated_causes.append(rc)
    result["root_causes"] = validated_causes

    validated_actions = []
    for ai in result.get("action_items", []):
        action_text = ai.get("action", "")
        generic_phrases = [
            "improve content quality", "add more content", "optimize for seo",
            "build backlinks", "create better content", "improve user experience",
            "enhance the page", "update regularly", "focus on quality",
        ]
        is_generic = any(gp in action_text.lower() for gp in generic_phrases)
        if is_generic and not ai.get("evidence_basis"):
            log.debug(f"Dropping generic action: {action_text[:60]}")
            continue
        validated_actions.append(ai)
    result["action_items"] = validated_actions

    max_confidence = min(1.0, sources_with_data / 7.0 + 0.15)
    raw_confidence = float(result.get("confidence", 0.5))
    result["confidence"] = round(min(raw_confidence, max_confidence), 2)

    if not result.get("data_gaps"):
        gaps = []
        for src_name, src_text in evidence_sources.items():
            if any(m.lower() in src_text.lower() for m in ["No ", "Error ", "unavailable", "not yet"]):
                gaps.append(src_name)
        result["data_gaps"] = gaps

    return result


async def generate_diagnosis(prompt_id: str, keyword: str, engine_name: str | None = None) -> dict | None:
    """
    Generate a full diagnosis for a keyword.
    If engine_name is provided, focuses on that engine. Otherwise analyzes all engines.
    """
    log.info(f"Generating diagnosis for: {keyword} (prompt={prompt_id})")

    # Gap 1: refresh citation-SERP overlaps before reading stale data
    try:
        await build_citation_overlaps(prompt_id)
    except Exception as e:
        log.warning(f"Overlap refresh failed (will use stale data): {e}")

    async def _safe_gather(coro, fallback: str, label: str):
        try:
            return await coro
        except Exception as e:
            log.warning(f"Evidence source '{label}' failed: {e}")
            return fallback

    (ai_evidence, raw_ai_evidence, coupon_evidence,
     serp_evidence, overlap_evidence, content_evidence,
     comparison_evidence, previous_diagnosis) = await asyncio.gather(
        _safe_gather(_gather_ai_evidence(prompt_id), "No AI engine data available.", "ai_evidence"),
        _safe_gather(_gather_raw_ai_responses(prompt_id), "No raw AI responses available.", "raw_ai_evidence"),
        _safe_gather(_gather_coupon_evidence(prompt_id), "No coupon data available.", "coupon_evidence"),
        _safe_gather(_gather_serp_evidence(prompt_id), "No SERP data available.", "serp_evidence"),
        _safe_gather(_gather_overlap_evidence(prompt_id), "No citation-SERP overlaps detected.", "overlap_evidence"),
        _safe_gather(_gather_content_evidence(prompt_id, keyword), "No content data available.", "content_evidence"),
        _safe_gather(_gather_comparison_evidence(prompt_id, keyword), "No competitor pages available.", "comparison_evidence"),
        _safe_gather(_gather_previous_diagnosis(prompt_id, engine_name or "all"), "No previous diagnosis exists.", "previous_diagnosis"),
    )

    settings = get_settings()
    target_status = _determine_target_status(ai_evidence, settings.target_domain)

    current_hash = _compute_evidence_hash(prompt_id, ai_evidence, serp_evidence)
    if not await evidence_hash_changed(prompt_id, engine_name or "all", current_hash):
        log.info(f"Evidence unchanged for {keyword} — skipping diagnosis (hash={current_hash[:8]})")
        return {"skipped": True, "reason": "evidence_unchanged", "evidence_hash": current_hash}

    evidence_sources = {
        "ai_engine_data": ai_evidence,
        "raw_ai_responses": raw_ai_evidence,
        "coupon_data": coupon_evidence,
        "serp_data": serp_evidence,
        "citation_overlaps": overlap_evidence,
        "content_comparison": content_evidence,
        "gap_analysis": comparison_evidence,
    }
    empty_markers = ["No AI engine data", "No raw AI responses", "No coupon data",
                     "No SERP data", "No citation-SERP",
                     "No content data", "No competitor pages", "Error gathering",
                     "unavailable", "not yet"]
    completeness_lines = []
    sources_with_data = 0
    for src_name, src_text in evidence_sources.items():
        has_data = not any(m.lower() in src_text.lower() for m in empty_markers)
        if has_data:
            sources_with_data += 1
            completeness_lines.append(f"  {src_name}: HAS DATA")
        else:
            completeness_lines.append(f"  {src_name}: EMPTY/UNAVAILABLE -- do NOT fabricate data for this source")
    completeness_lines.insert(0, f"Sources with real data: {sources_with_data}/7")
    if sources_with_data < 2:
        completeness_lines.append("WARNING: Very limited evidence. Keep confidence below 0.3. Only state what the available data shows.")
    evidence_completeness = "\n".join(completeness_lines)

    prompt_text = DIAGNOSIS_PROMPT.format(
        keyword=keyword,
        ai_evidence=ai_evidence,
        raw_ai_evidence=raw_ai_evidence,
        coupon_evidence=coupon_evidence,
        serp_evidence=serp_evidence,
        overlap_evidence=overlap_evidence,
        content_evidence=content_evidence,
        comparison_evidence=comparison_evidence,
        previous_diagnosis=previous_diagnosis,
        target_domain=settings.target_domain,
        target_status=target_status,
        evidence_completeness=evidence_completeness,
    )

    if sources_with_data < 2:
        log.warning(f"Insufficient evidence for diagnosis: {sources_with_data}/7 sources. Need at least 2.")
        return {"error": "insufficient_data", "sources_with_data": sources_with_data,
                "message": f"Only {sources_with_data}/7 evidence sources have data. Waiting for more scrape cycles."}

    if not settings.openai_api_key:
        log.warning("No OpenAI API key -- cannot generate diagnosis")
        return {"error": "no_api_key", "message": "No OpenAI API key configured"}

    try:
        import openai
        client = openai.OpenAI(api_key=settings.openai_api_key, timeout=60.0)
        model = "gpt-4o-mini"

        def _sync():
            return client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a precise AI Search Visibility analyst. Return valid JSON only, no markdown fences. ONLY state facts backed by the provided evidence data. Never fabricate URLs, numbers, or claims not present in the evidence."},
                    {"role": "user", "content": prompt_text},
                ],
                temperature=0.2,
                max_tokens=4000,
            )

        response = await asyncio.to_thread(_sync)
        usage = response.usage
        cost = 0.0
        if usage:
            cost = await _log_diagnosis_cost(model, usage.prompt_tokens, usage.completion_tokens)

        text = response.choices[0].message.content
        if not text:
            return {"error": "empty_response", "message": "LLM returned empty response"}

        text = text.strip()
        if text.startswith("```"):
            first_nl = text.find("\n")
            text = text[first_nl + 1:] if first_nl != -1 else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        result = json.loads(text)

        required_fields = {"root_causes", "action_items", "priority", "confidence", "summary"}
        missing = required_fields - set(result.keys())
        if missing:
            log.warning(f"Diagnosis LLM output missing fields: {missing}")
            for field in missing:
                if field in ("root_causes", "action_items"):
                    result[field] = []
                elif field == "confidence":
                    result[field] = 0.0
                elif field == "priority":
                    result[field] = "medium"
                else:
                    result[field] = ""

        result = _validate_diagnosis_output(result, sources_with_data, evidence_sources)

        evidence_snapshot = {
            "ai_evidence": ai_evidence[:2000],
            "raw_ai_evidence": raw_ai_evidence[:3000],
            "coupon_evidence": coupon_evidence[:1000],
            "serp_evidence": serp_evidence[:1000],
            "overlap_evidence": overlap_evidence[:1000],
            "content_evidence": content_evidence[:2000],
            "comparison_evidence": comparison_evidence[:2000],
            "target_status": target_status,
            "sources_with_data": sources_with_data,
        }

        diagnosis_row = await _save_diagnosis(
            prompt_id=prompt_id,
            engine_name=engine_name or "all",
            result=result,
            evidence_snapshot=evidence_snapshot,
            model=model,
            cost=cost,
            evidence_hash=current_hash,
        )

        return diagnosis_row

    except json.JSONDecodeError as e:
        log.warning(f"Diagnosis LLM returned invalid JSON: {e}")
        return {"error": "invalid_json", "message": str(e)}
    except Exception as e:
        log.error(f"Diagnosis generation failed: {e}")
        return {"error": "generation_failed", "message": str(e)}


async def _save_diagnosis(
    prompt_id: str,
    engine_name: str,
    result: dict,
    evidence_snapshot: dict,
    model: str,
    cost: float,
    evidence_hash: str = "",
) -> dict:
    """Persist diagnosis to DB. Supersedes previous active diagnosis, tracks revision count."""
    def _insert(conn):
        prev = conn.execute(
            """SELECT id, COALESCE(revision_count, 0) as rev
               FROM diagnoses
               WHERE prompt_id = %s::uuid AND engine_name = %s AND status = 'active'
               ORDER BY created_at DESC LIMIT 1""",
            (prompt_id, engine_name),
        ).fetchone()

        revision = (prev["rev"] + 1) if prev else 0

        if prev:
            conn.execute(
                """UPDATE diagnoses SET status = 'superseded'
                   WHERE prompt_id = %s::uuid AND engine_name = %s AND status = 'active'""",
                (prompt_id, engine_name),
            )

        cur = conn.execute(
            """INSERT INTO diagnoses
               (prompt_id, engine_name, root_causes, action_items,
                priority, confidence, summary, evidence_snapshot,
                llm_model, cost_usd, revision_count, evidence_hash)
               VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, created_at""",
            (
                prompt_id,
                engine_name,
                json.dumps(result.get("root_causes", [])),
                json.dumps(result.get("action_items", [])),
                result.get("priority", "medium"),
                result.get("confidence", 0.5),
                result.get("summary", ""),
                json.dumps(evidence_snapshot),
                model,
                cost,
                revision,
                evidence_hash,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return {**dict(row), "revision_count": revision}

    row = await run_db(_insert)
    if not row:
        log.error("Diagnosis INSERT returned no row")
        return {"error": "Save failed"}
    rev = row.get("revision_count", 0)
    log.info(f"Diagnosis saved: id={row['id']}, priority={result.get('priority')}, revision={rev}")

    return {
        "id": str(row["id"]),
        "prompt_id": prompt_id,
        "engine_name": engine_name,
        "root_causes": result.get("root_causes", []),
        "action_items": result.get("action_items", []),
        "priority": result.get("priority", "medium"),
        "confidence": result.get("confidence", 0.5),
        "summary": result.get("summary", ""),
        "created_at": str(row["created_at"]),
    }


async def get_diagnosis_priority_queue() -> list[dict]:
    """
    Get keywords ranked by diagnosis priority.
    Higher priority: target absent from AI, low rank, negative sentiment.
    Skips keywords diagnosed within last 6h unless new scrape data exists.
    """
    rows = await run_db(lambda conn: conn.execute(
        """WITH ai_stats AS (
            SELECT
                el.prompt_id,
                p.text as keyword,
                COUNT(DISTINCT el.engine_name) as engines_scraped,
                COUNT(*) FILTER (WHERE bm.is_target_brand) as target_mentions,
                AVG(bm.rank_position) FILTER (WHERE bm.is_target_brand) as target_avg_rank,
                COUNT(*) FILTER (WHERE bm.is_target_brand AND bm.sentiment = 'Negative') as negative_count,
                MAX(el.captured_at) as last_scraped
            FROM execution_logs el
            JOIN prompts p ON el.prompt_id = p.id
            LEFT JOIN brand_mentions bm ON bm.log_id = el.id
            WHERE el.captured_at >= NOW() - INTERVAL '7 days'
            GROUP BY el.prompt_id, p.text
        ),
        diag_stats AS (
            SELECT prompt_id, MAX(created_at) as last_diagnosed
            FROM diagnoses WHERE status = 'active'
            GROUP BY prompt_id
        )
        SELECT
            a.prompt_id, a.keyword, a.engines_scraped,
            a.target_mentions, a.target_avg_rank, a.negative_count,
            d.last_diagnosed,
            CASE
                WHEN a.target_mentions = 0 THEN 100
                WHEN a.negative_count > 0 THEN 80
                WHEN a.target_avg_rank > 3 THEN 60
                WHEN a.target_avg_rank > 1 THEN 40
                ELSE 20
            END as priority_score
        FROM ai_stats a
        LEFT JOIN diag_stats d ON a.prompt_id = d.prompt_id
        WHERE d.last_diagnosed IS NULL
           OR d.last_diagnosed < NOW() - INTERVAL '6 hours'
           OR a.last_scraped > d.last_diagnosed
        ORDER BY
            (d.last_diagnosed IS NULL) DESC,
            priority_score DESC,
            a.last_scraped DESC
        LIMIT 500""",
    ).fetchall())

    return [dict(r) for r in rows]
