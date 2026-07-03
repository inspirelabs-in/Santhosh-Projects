"""Presence-aware "welcome back" digest.

On each presence heartbeat the frontend calls ``touch_presence``. It bumps the
recruiter's ``last_seen_at`` and, if they've been away long enough (and weren't
shown a digest recently), gathers "what happened while you were away" from the
``domain_events`` log and creates a new conversation with the digest as its
first message.

The digest text itself is written by an LLM (4o-mini) from the resolved facts
gathered below -- real candidate names, roles, and reasons, not just counts.
The LLM is given the facts as JSON and told to use only what's there; if the
call fails for any reason, we fall back to a deterministic templated summary
built from the same facts, so a digest is always produced.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.digest import (
    AWAY_THRESHOLD_MINUTES,
    DIGEST_LOOKBACK_MAX_HOURS,
    DIGEST_MAX_EVENTS,
    DIGEST_QUIET_MINUTES,
    WELCOME_DIGEST_TOOL_MARKER,
)
from src.db.base import Application, Candidate, DomainEvent, RecruiterDigestState, Role
from src.db.repositories import recruiter_chat as repo
from src.llm.client import get_llm_client
from src.llm.prompts import WELCOME_DIGEST_V1, WELCOME_DIGEST_VERSION

logger = logging.getLogger(__name__)

# Raw ``domain_events.type`` strings grouped into human summary buckets, in
# display order. Matched by string (stable DB values), not enum members.
# Each entry: (singular_label, plural_label, {event_type_strings}).
# Used by both fact-gathering (to filter noise) and the fallback template.
_EVENT_BUCKETS: list[tuple[str, str, set[str]]] = [
    ("new applicant", "new applicants", {"applicant_intake", "triage_new_applicant"}),
    ("candidate advanced", "candidates advanced", {"stage_changed", "stage_advanced"}),
    ("voice screen completed", "voice screens completed", {"voice_evaluated"}),
    ("assignment submitted", "assignments submitted", {"assignment_submitted"}),
    ("interview scheduled", "interviews scheduled", {"interview_scheduled"}),
    ("interview analyzed", "interviews analyzed", {"meeting_analysis_ready"}),
    ("candidate hired", "candidates hired", {"hired"}),
    ("candidate rejected", "candidates rejected", {"rejected"}),
]

# Flat set of every event type we consider meaningful enough to surface in
# the digest -- excludes noise like "email_delivered".
_BUCKETED_EVENT_TYPES: set[str] = {t for _, _, kinds in _EVENT_BUCKETS for t in kinds}


class _DigestSummary(BaseModel):
    message_markdown: str


def _away_label(since: datetime, until: datetime) -> str:
    away_min = max(1, int((until - since).total_seconds() // 60))
    return f"{away_min}m" if away_min < 60 else f"{away_min // 60}h"


async def _gather_facts(
    session: AsyncSession, since: datetime, until: datetime
) -> dict[str, Any]:
    """Resolve real candidate/role/event facts for the digest -- deterministic,
    no LLM. Both the LLM renderer and the template fallback read from this.

    Two-part "since you left" snapshot:
      1. ``activity``      -- what happened in the window (since, until].
      2. ``new_decisions`` -- open action items that landed in the window
         (``created_at > since``); old pending items are folded into
         ``carryover_count`` (a number only, never re-listed).

    Only counts events/items whose application still exists -- the
    ``domain_events`` log is append-only, so events from deleted/withdrawn
    applications would otherwise show up as ghosts. The inner joins to
    ``applications`` and ``candidates`` drop those orphaned (and org-level
    null-app) rows.
    """
    activity_rows = (
        await session.execute(
            select(DomainEvent, Candidate.name, Role.title)
            .join(Application, Application.id == DomainEvent.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .outerjoin(Role, Role.id == Application.role_id)
            .where(
                DomainEvent.created_at > since,
                DomainEvent.created_at <= until,
                DomainEvent.type.in_(_BUCKETED_EVENT_TYPES),
            )
            .order_by(DomainEvent.created_at.desc())
            .limit(DIGEST_MAX_EVENTS)
        )
    ).all()

    activity: list[dict[str, Any]] = []
    seen_activity_apps: set[Any] = set()
    for ev, name, title in activity_rows:
        # Rows are already ordered by created_at desc, so the first row seen
        # per application is the most recent -- keep only that one.
        if ev.application_id in seen_activity_apps:
            continue
        seen_activity_apps.add(ev.application_id)
        activity.append(
            {
                "candidate": name or "(unknown)",
                "role": title,
                "type": ev.type,
                "from": ev.payload.get("from"),
                "to": ev.payload.get("to"),
                "when": ev.created_at.isoformat(),
            }
        )

    new_decision_rows = (
        await session.execute(
            select(DomainEvent, Candidate.name, Role.title)
            .join(Application, Application.id == DomainEvent.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .outerjoin(Role, Role.id == Application.role_id)
            .where(
                DomainEvent.requires_action.is_(True),
                DomainEvent.resolved_at.is_(None),
                DomainEvent.created_at > since,
            )
            .order_by(DomainEvent.created_at.asc())
        )
    ).all()

    new_decisions: list[dict[str, Any]] = []
    seen_decision_apps: set[Any] = set()
    for ev, name, title in new_decision_rows:
        if ev.application_id in seen_decision_apps:
            continue
        seen_decision_apps.add(ev.application_id)
        new_decisions.append(
            {
                "candidate": name or "(unknown)",
                "role": title,
                "reason": ev.payload.get("note") or ev.payload.get("reason") or ev.type,
                "since": ev.created_at.isoformat(),
            }
        )
    new_decisions_total = len(new_decisions)
    new_decisions = new_decisions[:10]

    carryover_count = (
        await session.execute(
            select(func.count())
            .select_from(DomainEvent)
            .join(Application, Application.id == DomainEvent.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .where(
                DomainEvent.requires_action.is_(True),
                DomainEvent.resolved_at.is_(None),
                DomainEvent.created_at <= since,
            )
        )
    ).scalar_one()

    return {
        "away_label": _away_label(since, until),
        "activity": activity,
        "new_decisions": new_decisions,
        "new_decisions_total": new_decisions_total,
        "carryover_count": carryover_count,
        "has_content": bool(activity or new_decisions),
    }


async def _render_llm(facts: dict[str, Any]) -> str:
    """Ask 4o-mini to turn the resolved facts into a crisp, grounded digest.

    The prompt lives in ``llm/prompts/welcome_digest.py`` (single source for all
    prompt templates). ``.format`` only substitutes ``{facts_json}``; braces
    inside the JSON value are not re-parsed.
    """
    client = get_llm_client()
    prompt = WELCOME_DIGEST_V1.format(facts_json=json.dumps(facts, default=str))
    result = await client.complete(
        prompt=prompt,
        response_model=_DigestSummary,
        trace_name="welcome_digest",
        prompt_version=WELCOME_DIGEST_VERSION,
        temperature=0.2,
        max_tokens=1200,
    )
    markdown = result.parsed.message_markdown.strip()
    # Append the carryover line deterministically -- the model is told NOT to add
    # it, so this is the single source of truth and can never print "and 0 more".
    carryover = facts["carryover_count"]
    if carryover > 0:
        markdown += f"\n\n_+ {carryover} older items still awaiting you._"
    return markdown


_EVENT_TYPE_TO_LABEL: dict[str, str] = {
    t: singular for singular, _, kinds in _EVENT_BUCKETS for t in kinds
}


def _activity_line(item: dict[str, Any]) -> str:
    """Human phrasing for a single deduped activity item."""
    candidate = item["candidate"]
    role = item.get("role")
    ev_type = item["type"]
    to = item.get("to")

    if ev_type in {"applicant_intake", "triage_new_applicant"}:
        return f"{candidate} — applied" + (f" for {role}" if role else "")
    if ev_type in {"stage_changed", "stage_advanced"} and to:
        return f"{candidate} — advanced to {to}"
    if ev_type == "hired":
        return f"{candidate} — hired"
    if ev_type == "rejected":
        return f"{candidate} — rejected"

    label = _EVENT_TYPE_TO_LABEL.get(ev_type, ev_type)
    return f"{candidate} — {label}"


def _render_fallback(facts: dict[str, Any]) -> str:
    """Deterministic template rendered from the same facts -- the safety net
    used when the LLM call fails for any reason.
    """
    lines: list[str] = [
        f"**Welcome back — here's what happened while you were away "
        f"(~{facts['away_label']}):**"
    ]

    if facts["activity"]:
        lines.append("\nWhat happened:")
        lines.extend(f"- {_activity_line(item)}" for item in facts["activity"])

    if facts["new_decisions"]:
        lines.append("\nNeeds your decision:")
        lines.extend(
            f"- {item['candidate']} — {item['reason']}" for item in facts["new_decisions"]
        )

    carryover_count = facts["carryover_count"]
    if carryover_count:
        lines.append(f"\n- + {carryover_count} older items still awaiting you")

    return "\n".join(lines)


async def touch_presence(
    session: AsyncSession, actor_hash: str, actor_role: str, *, check_digest: bool = True
) -> dict[str, Any] | None:
    """Heartbeat entry point. Always refreshes ``last_seen_at``.

    ``check_digest`` gates whether a "welcome back" digest may fire:
      - **False** (the periodic keep-alive ping while the tab is open): only
        refresh ``last_seen_at`` and return. A digest is NEVER created — this is
        what stops the "flood of Welcome back chats while actively using the app".
      - **True** (cold page load + tab-return via visibilitychange): compute the
        absence and, if long enough, create the catch-up conversation.

    So a digest fires ONLY when the recruiter returns after being away, never
    during continuous presence.

    Returned dict: ``{conversation_id}`` (``None`` when no digest is due).
    """
    now = datetime.now(UTC)

    # FOR UPDATE serializes concurrent heartbeats (multi-tab) so a digest can't
    # be computed twice for the same absence.
    state = (
        await session.execute(
            select(RecruiterDigestState)
            .where(RecruiterDigestState.actor_hash == actor_hash)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if state is None:
        # First-ever heartbeat: start tracking, nothing to catch up on.
        session.add(RecruiterDigestState(actor_hash=actor_hash, last_seen_at=now))
        return {"conversation_id": None}

    prev_seen = state.last_seen_at
    if prev_seen.tzinfo is None:  # defensive: treat naive as UTC
        prev_seen = prev_seen.replace(tzinfo=UTC)
    state.last_seen_at = now

    # Keep-alive ping (tab is open and active): only refresh last_seen, never
    # produce a digest. Digest evaluation happens exclusively on cold load /
    # tab-return (check_digest=True).
    if not check_digest:
        return {"conversation_id": None}

    away = now - prev_seen
    if away < timedelta(minutes=AWAY_THRESHOLD_MINUTES):
        return {"conversation_id": None}  # not away long enough

    last_digest = state.last_digest_at
    if last_digest is not None:
        if last_digest.tzinfo is None:
            last_digest = last_digest.replace(tzinfo=UTC)
        if now - last_digest < timedelta(minutes=DIGEST_QUIET_MINUTES):
            return {"conversation_id": None}  # shown recently

    since = max(prev_seen, now - timedelta(hours=DIGEST_LOOKBACK_MAX_HOURS))
    facts = await _gather_facts(session, since, now)
    if not facts["has_content"]:
        return {"conversation_id": None}  # nothing actually changed while away

    try:
        markdown = await _render_llm(facts)
    except Exception:
        logger.warning("digest LLM render failed; using template", exc_info=True)
        markdown = _render_fallback(facts)

    conv = await repo.create_conversation(
        session, actor_hash=actor_hash, actor_role=actor_role, title="Welcome back"
    )
    await repo.append_message(
        session,
        conversation_id=conv.id,
        role="assistant",
        tool_name=WELCOME_DIGEST_TOOL_MARKER,
        content=markdown,
    )
    state.last_digest_at = now
    return {"conversation_id": str(conv.id)}
