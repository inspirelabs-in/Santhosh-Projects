"""
Compare cited competitor pages vs target brand pages.
Uses LLM API (OpenAI/Groq) for structured content gap analysis.
"""
import asyncio
import hashlib
import json
import logging
import time

from app.agent.content_fetcher import fetch_page_content, get_cached_page
from app.config import get_settings
from app.database import run_db
from app.models import ComparisonResult, PageContent

log = logging.getLogger("geo.comparator")

_comparison_cache: dict[str, tuple[float, dict]] = {}
_COMPARISON_CACHE_TTL = 86400  # 24h


def _comparison_cache_key(keyword: str, competitor_url: str, target_url: str) -> str:
    raw = f"{keyword}|{competitor_url}|{target_url}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached_comparison(key: str) -> dict | None:
    entry = _comparison_cache.get(key)
    if entry and (time.time() - entry[0]) < _COMPARISON_CACHE_TTL:
        return entry[1]
    if entry:
        del _comparison_cache[key]
    return None

COST_PER_MILLION = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
}

COMPARISON_PROMPT = """You are an SEO/GEO content analyst. Compare two pages competing for the same search keyword.

CRITICAL RULES:
1. ONLY reference facts visible in the content previews and metadata below. NEVER assume content exists beyond what is shown.
2. Every claim must cite specific text, numbers, or structural elements from the data below.
3. If the content preview is truncated, say "based on visible content" -- do NOT guess what the rest contains.
4. Recommendations must reference specific observable differences, not generic SEO advice.

KEYWORD: {keyword}
ENGINE: {engine} (AI search engine that cited the competitor page)

COMPETITOR PAGE (cited by AI):
URL: {comp_url}
Title: {comp_title}
Word count: {comp_words}
Schema types: {comp_schemas}
Content preview:
{comp_text}

TARGET PAGE (our brand):
URL: {target_url}
Title: {target_title}
Word count: {target_words}
Schema types: {target_schemas}
Content preview:
{target_text}

Analyze WHY the AI engine cited the competitor page instead of the target page. ONLY cite observable differences from the data above.

Return JSON only:
{{
  "content_gaps": ["specific content the competitor has that target lacks -- quote or reference actual text/data from previews"],
  "structural_advantages": ["observable structural differences -- cite actual schema types, word counts, heading patterns from data above"],
  "authority_signals": ["trust signals visible in the content previews -- specific reviews, data points, citations you can see"],
  "recommendations": ["actionable steps referencing specific competitor data points above"],
  "confidence": 0.0 to 1.0
}}"""

NO_TARGET_PROMPT = """You are an SEO/GEO content analyst. Analyze why an AI engine cited a specific page for a keyword.

CRITICAL RULES:
1. ONLY describe what is visible in the content preview and metadata below. Do NOT assume content beyond what is shown.
2. Every claim must reference specific text, data, or structural elements from the content below.
3. If content is truncated, note this and limit analysis to what is visible.

KEYWORD: {keyword}
ENGINE: {engine}

CITED PAGE:
URL: {comp_url}
Title: {comp_title}
Word count: {comp_words}
Schema types: {comp_schemas}
Content preview:
{comp_text}

TARGET BRAND: {target_domain} (does not have a relevant page for this keyword yet)

Based ONLY on the observable content above, describe what makes this page effective and what the target brand needs to match.

Return JSON only:
{{
  "content_gaps": ["specific content elements visible in the preview that make this page effective -- cite actual text/data"],
  "structural_advantages": ["observable structural elements -- cite actual schema types, word count, patterns from data above"],
  "authority_signals": ["trust signals visible in the content preview -- specific data points you can see"],
  "recommendations": ["specific steps referencing the competitor's observable content above"],
  "confidence": 0.0 to 1.0
}}"""


async def _log_comparison_cost(model: str, input_tokens: int, output_tokens: int):
    rates = COST_PER_MILLION.get(model, {"input": 0, "output": 0})
    cost = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
    if cost <= 0:
        return
    def _insert(conn):
        conn.execute(
            """INSERT INTO api_costs (provider, model, input_tokens, output_tokens, cost_usd, purpose)
               VALUES (%s, %s, %s, %s, %s, 'comparison')""",
            ("openai", model, input_tokens, output_tokens, cost),
        )
        conn.commit()
    try:
        await run_db(_insert)
    except Exception as e:
        log.warning(f"Failed to log comparison cost: {e}")


async def _call_llm(prompt: str) -> dict | None:
    settings = get_settings()
    if not settings.openai_api_key:
        log.warning("No OpenAI API key configured for content comparison")
        return None

    try:
        import openai
        client = openai.OpenAI(api_key=settings.openai_api_key)
        model = "gpt-4o-mini"

        def _sync():
            return client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a precise SEO/GEO analyst. Return valid JSON only, no markdown. ONLY reference facts visible in the provided data. Never assume or fabricate content not shown in the previews."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=2048,
            )

        response = await asyncio.to_thread(_sync)
        usage = response.usage
        if usage:
            await _log_comparison_cost(model, usage.prompt_tokens, usage.completion_tokens)

        text = response.choices[0].message.content
        if not text:
            return None

        text = text.strip()
        if text.startswith("```"):
            first_nl = text.find("\n")
            text = text[first_nl + 1:] if first_nl != -1 else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        return json.loads(text)

    except json.JSONDecodeError as e:
        log.warning(f"LLM returned invalid JSON: {e}")
        return None
    except Exception as e:
        log.error(f"LLM comparison call failed: {e}")
        return None


def _truncate_text(text: str, max_chars: int = 3000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[...truncated]"


async def compare_pages(
    keyword: str,
    engine_name: str,
    competitor_url: str,
    target_url: str | None = None,
    competitor_content: PageContent | None = None,
    target_content: PageContent | None = None,
) -> ComparisonResult | None:
    """
    Compare a competitor's cited page against the target brand's page.
    If target_url is None, generates recommendations for creating a new page.
    """
    if not competitor_content:
        competitor_content = await get_cached_page(competitor_url)
    if not competitor_content or competitor_content.fetch_status != 200:
        log.debug(f"No cached content for competitor: {competitor_url}")
        return None

    if target_url and not target_content:
        target_content = await get_cached_page(target_url)

    if target_url and target_content and target_content.fetch_status == 200:
        prompt = COMPARISON_PROMPT.format(
            keyword=keyword,
            engine=engine_name,
            comp_url=competitor_url,
            comp_title=competitor_content.title,
            comp_words=competitor_content.word_count,
            comp_schemas=", ".join(competitor_content.schema_types) or "none",
            comp_text=_truncate_text(competitor_content.main_text_preview),
            target_url=target_url,
            target_title=target_content.title,
            target_words=target_content.word_count,
            target_schemas=", ".join(target_content.schema_types) or "none",
            target_text=_truncate_text(target_content.main_text_preview),
        )
    else:
        settings = get_settings()
        prompt = NO_TARGET_PROMPT.format(
            keyword=keyword,
            engine=engine_name,
            comp_url=competitor_url,
            comp_title=competitor_content.title,
            comp_words=competitor_content.word_count,
            comp_schemas=", ".join(competitor_content.schema_types) or "none",
            comp_text=_truncate_text(competitor_content.main_text_preview),
            target_domain=settings.target_domain,
        )

    cache_key = _comparison_cache_key(keyword, competitor_url, target_url or "")
    cached = _get_cached_comparison(cache_key)
    if cached:
        log.debug(f"Comparison cache hit: {keyword} / {competitor_url[:50]}")
        result = cached
    else:
        result = await _call_llm(prompt)
        if not result:
            return None
        _comparison_cache[cache_key] = (time.time(), result)

    return ComparisonResult(
        keyword=keyword,
        engine_name=engine_name,
        competitor_url=competitor_url,
        target_url=target_url or "",
        content_gaps=result.get("content_gaps", []),
        structural_advantages=result.get("structural_advantages", []),
        authority_signals=result.get("authority_signals", []),
        recommendations=result.get("recommendations", []),
        confidence=max(0.0, min(1.0, float(result.get("confidence", 0.5)))),
    )


async def analyze_citations_for_prompt(prompt_id: str, keyword: str) -> list[ComparisonResult]:
    """
    For a given prompt, find all AI-cited URLs, cross-reference with SERP,
    and run content comparison for each.
    """
    settings = get_settings()
    target_domain = settings.target_domain

    cited_rows = await run_db(lambda conn: conn.execute(
        """SELECT DISTINCT bm.cited_url, el.engine_name
           FROM brand_mentions bm
           JOIN execution_logs el ON bm.log_id = el.id
           WHERE el.prompt_id = %s::uuid
             AND bm.cited_url IS NOT NULL
             AND bm.cited_url != ''
             AND el.captured_at >= NOW() - INTERVAL '7 days'
           ORDER BY el.engine_name""",
        (prompt_id,),
    ).fetchall())

    if not cited_rows:
        log.info(f"No cited URLs found for prompt {prompt_id}")
        return []

    target_row = await run_db(lambda conn: conn.execute(
        """SELECT DISTINCT soe.url
           FROM serp_organic_entries soe
           JOIN serp_results sr ON soe.serp_id = sr.id
           WHERE sr.prompt_id = %s::uuid
             AND soe.domain ILIKE %s
           ORDER BY soe.url
           LIMIT 1""",
        (prompt_id, f"%{target_domain}%"),
    ).fetchone())
    target_url = target_row["url"] if target_row else None

    results = []
    compared = 0
    for row in cited_rows:
        url = row["cited_url"]
        engine = row["engine_name"]

        if target_domain.lower() in url.lower():
            continue

        if compared >= 5:
            break

        comparison = await compare_pages(
            keyword=keyword,
            engine_name=engine,
            competitor_url=url,
            target_url=target_url,
        )
        if comparison:
            results.append(comparison)
            compared += 1

    return results


async def build_citation_overlaps(prompt_id: str):
    """Cross-reference AI citations with SERP organic results for a prompt."""
    def _q(conn):
        return conn.execute(
            """SELECT bm.id as mention_id, bm.cited_url, el.engine_name
               FROM brand_mentions bm
               JOIN execution_logs el ON bm.log_id = el.id
               WHERE el.prompt_id = %s::uuid
                 AND bm.cited_url IS NOT NULL
                 AND bm.cited_url != ''
                 AND el.captured_at >= NOW() - INTERVAL '7 days'""",
            (prompt_id,),
        ).fetchall()
    citations = await run_db(_q)

    if not citations:
        return

    serp_entries = await run_db(lambda conn: conn.execute(
        """SELECT soe.id as entry_id, soe.url, soe.rank_position, soe.domain
           FROM serp_organic_entries soe
           JOIN serp_results sr ON soe.serp_id = sr.id
           WHERE sr.prompt_id = %s::uuid
             AND sr.captured_at = (
               SELECT MAX(captured_at) FROM serp_results WHERE prompt_id = %s::uuid
             )""",
        (prompt_id, prompt_id),
    ).fetchall())

    serp_url_map = {row["url"]: row for row in serp_entries}
    serp_domain_map = {}
    for row in serp_entries:
        d = row["domain"].lower()
        if d not in serp_domain_map or row["rank_position"] < serp_domain_map[d]["rank_position"]:
            serp_domain_map[d] = row

    def _extract_domain(raw: str) -> str:
        from urllib.parse import urlparse
        raw = raw.strip()
        if "://" in raw:
            try:
                return urlparse(raw).netloc.replace("www.", "").lower()
            except Exception:
                pass
        return raw.replace("www.", "").lower().split("/")[0]

    def _find_serp_match(url: str):
        match = serp_url_map.get(url)
        if match:
            return match
        domain = _extract_domain(url)
        match = serp_domain_map.get(domain)
        if match:
            return match
        if "." not in domain:
            for serp_d, entry in serp_domain_map.items():
                if serp_d.startswith(domain + ".") or serp_d == domain:
                    return entry
        return None

    def _insert_overlaps(conn):
        for cite in citations:
            url = cite["cited_url"]
            engine = cite["engine_name"]
            mention_id = cite["mention_id"]

            serp_match = _find_serp_match(url)

            serp_rank = serp_match["rank_position"] if serp_match else None
            entry_id = serp_match["entry_id"] if serp_match else None

            conn.execute(
                """INSERT INTO citation_serp_overlaps
                   (prompt_id, engine_name, cited_url, serp_rank, brand_mention_id, serp_entry_id)
                   VALUES (%s::uuid, %s, %s, %s, %s, %s)""",
                (prompt_id, engine, url, serp_rank, mention_id, entry_id),
            )
        conn.commit()

    try:
        await run_db(_insert_overlaps)
        log.info(f"Built {len(citations)} citation-SERP overlaps for prompt {prompt_id}")
    except Exception as e:
        log.error(f"Citation overlap build failed: {e}")
