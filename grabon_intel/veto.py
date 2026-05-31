"""Brand veto rules.

Three kinds:
  - block : do not discover, do not score, do not contact. e.g. competitors, blacklisted industries.
  - allow : if any allow rule exists, only matches pass discovery (whitelist mode).
  - existing_client : flagged for CSM, not for new outbound. Still researched.

Patterns:
  - exact domain match (root_domain)
  - simple glob with * (matches anywhere)
  - case-insensitive substring match on brand name when scope='name'

Decision order:
  1. existing_client + name/domain → return existing_client (still researched, no outbound)
  2. block → blocked
  3. if any allow rules exist for this kind and none match → blocked
  4. otherwise: allowed
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

VetoDecision = Literal["allowed", "blocked", "existing_client"]


@dataclass(slots=True)
class VetoResult:
    decision: VetoDecision
    matched_rule_id: int | None = None
    reason: str | None = None


async def evaluate(session: AsyncSession, *, name: str | None, domain: str | None) -> VetoResult:
    domain_l = (domain or "").lower().removeprefix("www.")
    name_l = (name or "").lower()

    rows = (
        await session.execute(
            text("SELECT id, kind, pattern, scope, reason FROM brand_blocklist ORDER BY id")
        )
    ).all()

    block_match: tuple[int, str] | None = None
    existing_match: tuple[int, str] | None = None
    allow_present_for_kind = False
    allow_matched = False

    for rid, kind, pattern, scope, reason in rows:
        pat_l = (pattern or "").lower()
        target = domain_l if scope == "domain" else name_l
        if not target:
            continue
        hit = False
        if "*" in pat_l or "?" in pat_l:
            hit = fnmatch.fnmatchcase(target, pat_l)
        elif scope == "name":
            hit = pat_l in target
        else:
            hit = pat_l == target
        if not hit:
            continue
        if kind == "existing_client" and existing_match is None:
            existing_match = (int(rid), reason or "existing client")
        elif kind == "block" and block_match is None:
            block_match = (int(rid), reason or "blocklisted")
        elif kind == "allow":
            allow_present_for_kind = True
            allow_matched = True

    # Track allow presence even when not matched.
    if not allow_present_for_kind:
        allow_present_for_kind = any(r[1] == "allow" for r in rows)

    if existing_match:
        return VetoResult("existing_client", matched_rule_id=existing_match[0], reason=existing_match[1])
    if block_match:
        return VetoResult("blocked", matched_rule_id=block_match[0], reason=block_match[1])
    if allow_present_for_kind and not allow_matched:
        return VetoResult("blocked", reason="not on allowlist")
    return VetoResult("allowed")
