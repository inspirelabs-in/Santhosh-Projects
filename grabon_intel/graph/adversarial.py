"""Adversarial discovery — runs the supervisor *from a competitor's POV*.

Strategy:
  1. Given a Grabon-style ICP, ask an LLM to enumerate the *complement*:
     ICPs we are *not* actively targeting but a savvy competitor would.
  2. For each, propose seed queries.
  3. Persist these as `adversarial_proposals` (light row in `events`
     so they show up in the workspace inbox).

Cheap, runs weekly. Surfaces blind spots without booking compute on a
full DossierWF.
"""
from __future__ import annotations

import dataclasses
import json
from typing import Any

from ..events import emit
from ..db import session as session_ctx
from ..llm import Tier, complete
from ..logging import get_logger

log = get_logger(__name__)


@dataclasses.dataclass(slots=True)
class AdversarialResult:
    proposals: list[dict[str, Any]]
    cost_cents: int


_PROMPT = (
    "Grabon's ICP today: D2C / mid-market consumer brands in India with "
    "monthly digital ad spend, recent funding, or aggressive growth — "
    "categories include beauty, audio, food, fintech consumer, edtech, gaming, quick-commerce.\n\n"
    "Acting as a competing growth-marketing agency, list 6 ICP segments Grabon is "
    "*under-targeting* and explain why each is high-leverage. For each, suggest 2 "
    "seed search queries to find candidates. Return strict JSON: "
    "{proposals:[{segment, rationale, expected_lift, queries:[2]}]}."
)


async def run_adversarial() -> AdversarialResult:
    res = await complete(
        tier=Tier.SMART, prompt=_PROMPT, json_mode=True, max_output_tokens=1200, temperature=0.3
    )
    try:
        data = json.loads(res.content)
    except Exception:
        data = {"proposals": [], "_raw": res.content[:2000]}
    proposals = list(data.get("proposals") or [])
    async with session_ctx() as s:
        await emit(
            s,
            topic="adversarial.proposals",
            brand_id=None,
            payload={"proposals": proposals, "model": res.model_id},
        )
    return AdversarialResult(proposals=proposals, cost_cents=res.cost_cents)
