"""Multi-LLM ensemble scoring.

Calls 3 tiers in parallel (cheap, fast, smart), parses each result as
JSON, then computes:
  - mean total (numeric vote)
  - tier mode (categorical vote)
  - disagreement = max(total) - min(total)
  - confidence = mean(per-model confidence) downweighted by disagreement/100

When disagreement > 20 points or no agreement on tier, mark
`needs_review=True` so the UI surfaces it to a human.

Cost note: ensembles ~3x scoring spend per dossier. Use selectively —
recommend only when single-model confidence < 0.7.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .router import Tier, complete


@dataclass(slots=True)
class EnsembleVote:
    total: int | None
    tier: str | None
    confidence: float
    disagreement: int
    needs_review: bool
    per_model: list[dict[str, Any]]
    total_cost_cents: int


_TIERS = (Tier.CHEAP, Tier.FAST, Tier.SMART)


async def score_ensemble(prompt: str, *, max_output_tokens: int = 600) -> EnsembleVote:
    results = await asyncio.gather(
        *(complete(tier=t, prompt=prompt, json_mode=True, max_output_tokens=max_output_tokens) for t in _TIERS),
        return_exceptions=True,
    )

    parsed: list[dict[str, Any]] = []
    total_cost = 0
    for t, res in zip(_TIERS, results, strict=True):
        if isinstance(res, Exception):
            parsed.append({"tier": t.value, "error": f"{type(res).__name__}: {res}", "ok": False})
            continue
        total_cost += res.cost_cents
        try:
            data = json.loads(res.content)
        except Exception:
            parsed.append({"tier": t.value, "model": res.model_id, "ok": False, "raw": res.content[:300]})
            continue
        parsed.append(
            {
                "tier": t.value,
                "model": res.model_id,
                "ok": True,
                "total": data.get("total"),
                "tier_label": data.get("tier"),
                "confidence": data.get("confidence"),
            }
        )

    oks = [p for p in parsed if p.get("ok") and isinstance(p.get("total"), (int, float))]
    if not oks:
        return EnsembleVote(None, None, 0.0, 0, True, parsed, total_cost)

    totals = [int(p["total"]) for p in oks]
    mean_total = int(round(sum(totals) / len(totals)))
    disagreement = max(totals) - min(totals)
    tier_counter = Counter(p["tier_label"] for p in oks if p.get("tier_label"))
    tier_mode = tier_counter.most_common(1)[0][0] if tier_counter else None
    confs = [float(p["confidence"]) for p in oks if isinstance(p.get("confidence"), (int, float))]
    base_conf = (sum(confs) / len(confs)) if confs else 0.5
    confidence = max(0.0, min(1.0, base_conf - disagreement / 100))
    needs_review = disagreement > 20 or (len(tier_counter) > 1 and tier_counter.most_common(1)[0][1] < 2)

    return EnsembleVote(
        total=mean_total,
        tier=tier_mode,
        confidence=confidence,
        disagreement=disagreement,
        needs_review=needs_review,
        per_model=parsed,
        total_cost_cents=total_cost,
    )
