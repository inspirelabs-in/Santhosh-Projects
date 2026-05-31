"""Prompt loader.

Reads the active version of a named prompt from DB; falls back to a
hardcoded default if the table is empty / unreachable. Cached in-process
for 60s — short enough to roll new versions fast, long enough to avoid
hammering the DB on every LLM call.

Tradeoff with module-level constants: DB-backed prompts let us A/B and
canary in production without redeploy. The cost is a DB query per prompt
miss, which is sub-ms.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from ..db import session as session_ctx
from ..logging import get_logger

log = get_logger(__name__)

_CACHE: dict[str, tuple[str, int, float]] = {}
_TTL = 60.0


@dataclass(slots=True)
class PromptRef:
    name: str
    version: int
    body: str


_DEFAULTS: dict[str, str] = {
    # Keep the prompt strings here as the source of truth seed. The DB
    # entry, when present, shadows these — but a fresh install runs fine.
    "research.system": (
        "You are a senior brand analyst. Build a structured profile from the "
        "evidence below. Output strict JSON with keys: company "
        "{legal_name, brand_name, domain, hq, geos, employees_est, revenue_band, "
        "funding_stage}, positioning {category, sub_category, audience, price_band, "
        "USP_summary}, digital_footprint {channels_active[], martech_stack[], "
        "web_perf_signal, seo_signal, paid_signal, social_signal, email_signal}."
    ),
    "intent.system": (
        "You classify revenue-team requests into one intent + arguments. "
        "Intents: discover, dossier, query, draft, rescore, help. "
        "Output strict JSON: {intent, confidence(0-1), args, explanation}."
    ),
    "outreach.system": (
        "Apply Grabon FORGE-COLD-EMAIL rules. Subject 3-5 words. Body <75 words. "
        "Open with prospect, not us. No filler. Output strict JSON."
    ),
}


async def get_prompt(name: str) -> PromptRef:
    now = time.monotonic()
    cached = _CACHE.get(name)
    if cached and now - cached[2] < _TTL:
        return PromptRef(name=name, version=cached[1], body=cached[0])

    try:
        async with session_ctx() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT version, body FROM prompts "
                        "WHERE name = :n AND active ORDER BY version DESC LIMIT 1"
                    ),
                    {"n": name},
                )
            ).first()
    except Exception as exc:  # noqa: BLE001
        log.warning("prompt.load_failed", name=name, exc=str(exc))
        row = None

    if row:
        body, version = str(row[1]), int(row[0])
    else:
        body, version = _DEFAULTS.get(name, ""), 0
    _CACHE[name] = (body, version, now)
    return PromptRef(name=name, version=version, body=body)


async def upsert_prompt(name: str, body: str, *, notes: str | None = None) -> int:
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "WITH next AS ("
                    "  SELECT COALESCE(MAX(version), 0) + 1 AS v FROM prompts WHERE name = :n"
                    ") "
                    "INSERT INTO prompts (name, version, body, active, notes) "
                    "SELECT :n, next.v, :b, FALSE, :nt FROM next RETURNING version"
                ),
                {"n": name, "b": body, "nt": notes},
            )
        ).first()
    _CACHE.pop(name, None)
    return int(row[0]) if row else 0


async def set_active_version(name: str, version: int) -> bool:
    async with session_ctx() as s:
        await s.execute(text("UPDATE prompts SET active = FALSE WHERE name = :n"), {"n": name})
        r = (
            await s.execute(
                text(
                    "UPDATE prompts SET active = TRUE WHERE name = :n AND version = :v RETURNING id"
                ),
                {"n": name, "v": version},
            )
        ).first()
    _CACHE.pop(name, None)
    return r is not None


def clear_cache() -> None:
    _CACHE.clear()


_ = Any  # placate strict imports
