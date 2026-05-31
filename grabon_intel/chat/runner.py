"""Chat runner. Produces an async stream of `ChatEvent` objects which the
FastAPI route serialises as SSE.

Key changes from the original fire-and-forget design:
- Runs the research graph INLINE when Temporal is unavailable (or always for
  single-lead use). Returns full dossier data directly in the SSE stream.
- Auto-creates brands when they don't exist in the DB.
- Falls back gracefully when infrastructure (DB/Temporal) is down.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import get_settings
from ..llm import Tier, complete, stream_complete
from ..logging import get_logger
from ..resolver import normalize_domain
from .intents import Intent, classify

log = get_logger(__name__)

# Regex backstop: "research <1-3 words>" without plural/list words → force dossier
_SINGLE_BRAND_RE = re.compile(
    r"^research\s+([a-z0-9][\w.\-]{0,30}(?:\s+[a-z0-9][\w.\-]{0,30}){0,2})$", re.I
)
_PLURAL_WORDS = {"brands", "companies", "competitors", "stores", "shops", "products", "best", "top", "find", "list"}


@dataclass(slots=True)
class ChatRequest:
    message: str
    user: str = "anonymous"
    history: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class ChatEvent:
    type: str  # intent | action | result | answer | error | progress | done
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _maybe_override_to_dossier(msg: str, cls):
    """Override discover→dossier when message is clearly about a single brand."""
    m = _SINGLE_BRAND_RE.match(msg.strip())
    if not m:
        return cls
    words = set(m.group(1).lower().split())
    if words & _PLURAL_WORDS:
        return cls
    from .intents import IntentClassification, Intent
    return IntentClassification(
        intent=Intent.DOSSIER,
        confidence=0.95,
        args={"brand_name": m.group(1).strip()},
        explanation=f"regex_override: '{m.group(1).strip()}' looks like a single brand",
    )


async def run_chat(req: ChatRequest) -> AsyncIterator[ChatEvent]:
    cls = await classify(req.message, history=req.history or None)

    # Backstop: reclassify "research <brand>" from discover→dossier
    if cls.intent is Intent.DISCOVER:
        cls = _maybe_override_to_dossier(req.message, cls)

    yield ChatEvent(
        type="intent",
        data={
            "intent": cls.intent.value,
            "confidence": cls.confidence,
            "args": cls.args,
            "explanation": cls.explanation,
        },
    )
    try:
        if cls.intent is Intent.DISCOVER:
            async for ev in _handle_discover(cls.args, history=req.history):
                yield ev
        elif cls.intent is Intent.DOSSIER:
            async for ev in _handle_dossier(cls.args):
                yield ev
        elif cls.intent is Intent.RESCORE:
            async for ev in _handle_dossier({**cls.args, "reason": "rescore"}):
                yield ev
        elif cls.intent is Intent.DRAFT:
            async for ev in _handle_draft(cls.args):
                yield ev
        elif cls.intent is Intent.HELP:
            yield ChatEvent(type="answer", data={"text": _HELP_TEXT})
        else:
            async for ev in _handle_query(
                cls.args.get("natural_language") or req.message,
                history=req.history,
            ):
                yield ev
    except Exception as exc:  # noqa: BLE001
        log.exception("chat.handler_failed", intent=cls.intent.value)
        yield ChatEvent(type="error", data={"message": f"{type(exc).__name__}: {exc}"})
    yield ChatEvent(type="done")


# --- helpers ------------------------------------------------------------------


async def _resolve_or_create_brand(
    name: str | None, domain: str | None
) -> tuple[int | None, str | None, str | None]:
    """Try DB resolution. Auto-creates brand if not found. Returns (brand_id, name, domain).
    If DB is unavailable, returns (None, name, domain) — caller can still proceed."""
    try:
        from ..db import session as session_ctx
        from ..resolver import resolve_or_create

        rd = normalize_domain(domain)
        async with session_ctx() as ses:
            brand_id = await resolve_or_create(ses, name=name, domain=rd, enforce_veto=False)
            return brand_id, name, rd
    except Exception as exc:
        log.warning("chat.brand_resolve_failed", exc=str(exc))
        return None, name, normalize_domain(domain)


_NODE_LABELS = {
    "research": "Researching company profile & digital footprint",
    "competitor": "Analyzing competitive landscape",
    "opportunity": "Identifying growth opportunities",
    "score": "Scoring lead quality",
    "outreach": "Generating outreach sequence",
}


async def _run_research_inline(
    brand_id: int | None, name: str | None, domain: str | None, reason: str = "chat"
) -> AsyncIterator[ChatEvent]:
    """Run the 5-node research graph with per-node progress."""
    from ..graph.supervisor import run_research_streaming

    yield ChatEvent(type="progress", data={"step": "research_started", "brand": name or domain})

    result = None
    try:
        async for node_name, node_data in run_research_streaming(
            brand_id=brand_id or 0,
            brand_name=name,
            brand_domain=domain,
            reason=reason,
        ):
            if node_name == "__done__":
                result = node_data["result"]
                break
            label = _NODE_LABELS.get(node_name, node_name)
            yield ChatEvent(type="progress", data={
                "step": f"node_{node_name}_done",
                "message": f"✓ {label}",
                "node": node_name,
            })
    except Exception as exc:
        yield ChatEvent(type="error", data={"message": f"research_failed: {exc}"})
        return

    if result is None:
        yield ChatEvent(type="error", data={"message": "research_failed: no result"})
        return

    # Persist dossier — create brand if needed
    dossier_id = None
    try:
        from ..db import session as session_ctx
        from ..resolver import resolve_or_create, normalize_domain
        from sqlalchemy import text as sql_text

        # If no brand_id yet, create brand from research results or original hints
        if not brand_id:
            research_co = (result.research or {}).get("company", {})
            resolved_name = research_co.get("brand_name") or name
            resolved_domain = normalize_domain(research_co.get("domain")) or domain
            async with session_ctx() as ses:
                brand_id = await resolve_or_create(ses, name=resolved_name, domain=resolved_domain, enforce_veto=False)
            if brand_id:
                name = resolved_name
                domain = resolved_domain

        if brand_id:
            dossier_data = {
                "research": result.research,
                "competitor": result.competitor,
                "opportunity": result.opportunity,
                "score": result.score,
                "outreach": result.outreach,
            }
            async with session_ctx() as ses:
                row = (await ses.execute(
                    sql_text("SELECT COALESCE(MAX(version), 0) + 1 FROM dossiers WHERE brand_id = :b"),
                    {"b": brand_id},
                )).scalar()
                version = row or 1
                ins = await ses.execute(
                    sql_text(
                        "INSERT INTO dossiers (brand_id, version, data, cost_cents) "
                        "VALUES (:b, :v, :d, :c) RETURNING id"
                    ),
                    {"b": brand_id, "v": version, "d": json.dumps(dossier_data), "c": result.total_cost_cents},
                )
                dossier_id = ins.scalar()

                # Update brand domain if research discovered it
                if domain:
                    await ses.execute(
                        sql_text("UPDATE brands SET domain = :d, root_domain = :d WHERE id = :b AND (domain IS NULL OR domain = '')"),
                        {"d": domain, "b": brand_id},
                    )
    except Exception as exc:
        log.warning("chat.dossier_persist_failed", exc=str(exc))

    # Track which data sources were degraded (LLM-inferred, not evidence-backed)
    degraded_sources = []
    for n in result.nodes:
        if n.get("node") == "research":
            tools_used = n.get("tools_used", [])
            if "searxng" not in tools_used:
                degraded_sources.append("search")
            if "wappalyzer" not in tools_used:
                degraded_sources.append("tech_stack")
        if n.get("node") == "competitor" and "searxng" not in (n.get("tools_used") or []):
            degraded_sources.append("competitor_serp")
        if n.get("node") == "opportunity" and "google_news" not in (n.get("tools_used") or []):
            degraded_sources.append("news")

    yield ChatEvent(
        type="result",
        data={
            "dossier_id": dossier_id,
            "brand_id": brand_id,
            "brand_name": name,
            "brand_domain": domain,
            "total_cost_cents": result.total_cost_cents,
            "nodes_completed": [n.get("node") for n in result.nodes],
            "degraded_sources": degraded_sources,
            "research": result.research,
            "competitor": result.competitor,
            "opportunity": result.opportunity,
            "score": result.score,
            "outreach": result.outreach,
        },
    )


# --- handlers -----------------------------------------------------------------


_DISCOVER_SYSTEM = """You help identify real brand names for a market research team.
Given a user query about finding brands/companies, use ANY context provided (search results, your knowledge)
to list specific, real brand names that match. Output strict JSON:
{"brands": [{"name": "...", "domain": "...", "reason": "one line why it matches"}], "summary": "one line overview"}
List 5-10 brands. Only real companies. No generic categories. domain can be null if unknown."""


async def _handle_discover(
    args: dict[str, Any],
    history: list[dict[str, str]] | None = None,
) -> AsyncIterator[ChatEvent]:
    search_terms = args.get("params", {}).get("search_terms") or args.get("natural_language") or ""
    yield ChatEvent(type="action", data={"action": "discover", "search_terms": search_terms})
    yield ChatEvent(type="progress", data={"message": "Searching for matching brands..."})

    # Try SearXNG for fresh context
    search_context = ""
    try:
        import httpx
        s = get_settings()
        searxng_url = getattr(s, "searxng_url", None) or os.environ.get("SEARXNG_URL", "http://searxng:8080")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{searxng_url}/search",
                params={"q": search_terms or "Indian D2C brands", "format": "json", "categories": "general"},
            )
            if resp.status_code == 200:
                results = resp.json().get("results", [])[:8]
                search_context = "\n".join(
                    f"- {r.get('title', '')}: {r.get('snippet') or r.get('content') or ''}".rstrip()[:200] for r in results
                )
    except Exception as exc:
        log.warning("discover.searxng_failed", exc=str(exc))

    # Build prompt with search context
    user_prompt = f"User query: {search_terms}"
    if search_context:
        user_prompt += f"\n\nSearch results for context:\n{search_context}"

    # Build prompt with history context
    prompt_parts: list[str] = []
    if history:
        for turn in history[-6:]:
            role = turn.get("role", "user").capitalize()
            prompt_parts.append(f"{role}: {turn.get('content', '')}")
    prompt_parts.append(user_prompt)
    prompt_parts.append("\nReturn JSON only.")

    try:
        res = await complete(
            tier=Tier.FAST,
            system=_DISCOVER_SYSTEM,
            prompt="\n".join(prompt_parts),
            json_mode=True,
            max_output_tokens=800,
        )
        data = json.loads(res.content)
        brands = data.get("brands", [])
        summary = data.get("summary", "")

        # Stream a readable answer
        answer_parts = []
        if summary:
            answer_parts.append(f"**{summary}**\n\n")
        for i, b in enumerate(brands, 1):
            line = f"{i}. **{b['name']}**"
            if b.get("domain"):
                line += f" ({b['domain']})"
            if b.get("reason"):
                line += f" — {b['reason']}"
            answer_parts.append(line)
        answer_parts.append("\n\n*Click a brand name or type \"research [brand]\" to get a full dossier.*")
        answer_text = "\n".join(answer_parts)

        for chunk in [answer_text[i:i+80] for i in range(0, len(answer_text), 80)]:
            yield ChatEvent(type="token", data={"text": chunk})
        yield ChatEvent(type="answer", data={"text": "", "streamed": True, "brands": brands})
    except Exception as exc:
        log.warning("discover.llm_failed", exc=str(exc))
        yield ChatEvent(type="error", data={"message": f"Could not find brands: {exc}"})


async def _handle_dossier(args: dict[str, Any]) -> AsyncIterator[ChatEvent]:
    brand_id = args.get("brand_id")
    name = args.get("brand_name")
    domain = normalize_domain(args.get("brand_domain")) if args.get("brand_domain") else None
    reason = args.get("reason") or "chat"

    # If name looks like a domain (e.g. classifier sent "boat-lifestyle.com" as name), clean up
    if name and not domain and "." in name and " " not in name:
        domain = normalize_domain(name)
        name = None
    # If name == domain, clear name so LLM uses domain to infer real brand name
    if name and domain and normalize_domain(name) == domain:
        name = None

    # Auto-resolve or create brand
    if not brand_id:
        brand_id, name, domain = await _resolve_or_create_brand(name, domain)

    # Even without brand_id (DB down), we can still research by name/domain
    if not brand_id and not name and not domain:
        yield ChatEvent(type="error", data={"message": "need at least brand_name or brand_domain to research"})
        return

    yield ChatEvent(type="action", data={
        "action": "research",
        "brand_id": brand_id,
        "brand_name": name,
        "brand_domain": domain,
        "reason": reason,
    })

    # Try Temporal first, fall back to inline
    force_inline = os.environ.get("GRABON_FORCE_INLINE", "").lower() in ("1", "true", "yes")
    if not brand_id:
        force_inline = True  # Temporal requires brand_id; fall back to inline
    temporal_ok = False
    try:
        if force_inline:
            raise RuntimeError("forced inline — no brand_id or GRABON_FORCE_INLINE")
        from temporalio.client import Client, TLSConfig
        from ..workflows import DossierWF, DossierWFInput

        s = get_settings()
        client = await Client.connect(
            s.temporal_address, namespace=s.temporal_namespace,
            tls=TLSConfig() if s.temporal_tls else False,
        )
        handle = await client.start_workflow(
            DossierWF.run,
            DossierWFInput(brand_id=brand_id, reason=reason, brand_hint={"name": name, "domain": domain}),
            id=f"chat-dossier-{brand_id}-{__import__('time').time_ns()}",
            task_queue=s.temporal_task_queue,
        )
        temporal_ok = True
        # Wait for workflow result (timeout 120s)
        yield ChatEvent(type="progress", data={"step": "workflow_started", "workflow_id": handle.id})
        try:
            wf_result = await asyncio.wait_for(handle.result(), timeout=120.0)
            yield ChatEvent(type="result", data={"workflow_result": wf_result, "brand_id": brand_id, "status": "completed"})
        except asyncio.TimeoutError:
            yield ChatEvent(type="result", data={
                "workflow_id": handle.id,
                "run_id": handle.first_execution_run_id,
                "brand_id": brand_id,
                "status": "started_async",
                "message": "Research running in background — check brand panel for live progress.",
            })
    except Exception as exc:
        if not temporal_ok:
            log.warning("chat.temporal_unavailable", exc=str(exc))
            yield ChatEvent(type="progress", data={"message": "Starting research pipeline..."})
            async for ev in _run_research_inline(brand_id, name, domain, reason):
                yield ev


async def _handle_draft(args: dict[str, Any]) -> AsyncIterator[ChatEvent]:
    brand_id = args.get("brand_id")
    name = args.get("brand_name")
    channel = (args.get("channel") or "email").lower()

    # Resolve brand
    if not brand_id and name:
        brand_id, name, _ = await _resolve_or_create_brand(name, args.get("brand_domain"))

    # Try to pull latest dossier as context
    opportunity = {}
    score = {}
    try:
        from ..db import session as session_ctx
        from sqlalchemy import text as sql_text

        if brand_id:
            async with session_ctx() as ses:
                doss = (
                    await ses.execute(
                        sql_text(
                            "SELECT data FROM dossiers WHERE brand_id = :b "
                            "ORDER BY version DESC LIMIT 1"
                        ),
                        {"b": brand_id},
                    )
                ).first()
                if doss:
                    opportunity = (doss[0].get("opportunity") if doss else None) or {}
                    score = (doss[0].get("score") if doss else None) or {}
    except Exception as exc:
        log.warning("chat.draft_context_failed", exc=str(exc))

    # If no dossier context, run a quick research first
    if not opportunity and (name or args.get("brand_domain")):
        yield ChatEvent(type="progress", data={"message": "No prior research found. Running quick research first..."})
        domain = normalize_domain(args.get("brand_domain") or name)
        async for ev in _run_research_inline(brand_id, name, domain, "draft_prep"):
            if ev.type == "result":
                opportunity = ev.data.get("opportunity") or {}
                score = ev.data.get("score") or {}
            yield ev

    yield ChatEvent(type="action", data={"action": "draft", "channel": channel, "brand_id": brand_id, "brand_name": name})
    prompt = (
        f"Channel: {channel}. Apply Grabon FORGE-COLD-EMAIL rules. "
        "Open with prospect. Subject 3-5 words. Body <75 words. No filler.\n\n"
        f"Brand: {name}\n"
        f"Opportunity: {json.dumps(opportunity)[:2500]}\n\nScore: {json.dumps(score)[:600]}\n\n"
        "Output strict JSON: {subject, body, cta}."
    )
    res = await complete(tier=Tier.SMART, prompt=prompt, json_mode=True, max_output_tokens=600)
    yield ChatEvent(type="result", data={"draft": res.content, "cost_cents": res.cost_cents, "model": res.model_id})


_QUERY_SYSTEM = """You are the GrabOn Intel assistant — you answer revenue-team questions about brands, signals, and dossiers.
Use the database context provided. If the context is empty, say so honestly — do NOT invent facts.
If asked about system capabilities, describe what this agent can do.
You have conversational memory — use prior messages to understand follow-up questions.
Be concise, specific, and helpful. Use markdown formatting when appropriate."""


async def _handle_query(
    text_q: str,
    history: list[dict[str, str]] | None = None,
) -> AsyncIterator[ChatEvent]:
    yield ChatEvent(type="action", data={"action": "read_db", "query": text_q})

    # Best-effort context fetch
    rows: list[dict[str, Any]] = []
    try:
        from ..db import session as session_ctx
        from sqlalchemy import text as sql_text

        async with session_ctx() as ses:
            rs = (
                await ses.execute(
                    sql_text(
                        "SELECT id, name, domain, status FROM brands "
                        "WHERE name ILIKE :q OR domain ILIKE :q "
                        "ORDER BY id DESC LIMIT 5"
                    ),
                    {"q": f"%{text_q[:60]}%"},
                )
            ).mappings().all()
            rows = [dict(r) for r in rs]
    except Exception as exc:
        log.warning("query.context_fetch_failed", exc=str(exc))

    context_note = f"\n\nContext (recent matching brands): {rows}" if rows else ""

    # Build multi-turn messages for conversational context
    chat_messages: list[dict[str, str]] = []
    if history:
        for turn in history[-10:]:
            chat_messages.append({
                "role": turn.get("role", "user"),
                "content": turn.get("content", ""),
            })
    chat_messages.append({
        "role": "user",
        "content": f"{text_q}{context_note}\n\nAnswer concisely (<=4 sentences). Cite brand ids if used.",
    })

    try:
        async for chunk in stream_complete(
            tier=Tier.FAST,
            system=_QUERY_SYSTEM,
            messages=chat_messages,
            max_output_tokens=600,
        ):
            yield ChatEvent(type="token", data={"text": chunk})
        yield ChatEvent(type="answer", data={"text": "", "streamed": True})
    except Exception as exc:
        log.warning("query.stream_failed", exc=str(exc))
        # Fallback to non-streaming
        res = await complete(
            tier=Tier.FAST,
            system=_QUERY_SYSTEM,
            prompt=f"{text_q}{context_note}\n\nAnswer concisely (<=4 sentences).",
            max_output_tokens=600,
        )
        yield ChatEvent(type="answer", data={"text": res.content, "cost_cents": res.cost_cents})


_HELP_TEXT = (
    "I can:\n"
    "1. **Find brands** — 'find 20 D2C beauty brands' (runs signal collectors)\n"
    "2. **Research a brand** — 'research mamaearth.in' (full dossier: profile, competitors, opportunity, score, outreach)\n"
    "3. **Draft outreach** — 'email Boat about ad-creative refresh' (generates cold email sequence)\n"
    "4. **Rescore** — 'rescore brand X' (re-run scoring only)\n"
    "5. **Answer questions** — 'which brands scored above 70?' (queries stored data)\n\n"
    "I work best when you give me a brand name or domain. Example: 'research plum goodness'"
)


# Helper kept for asyncio.sleep wiring in tests
_ = asyncio
