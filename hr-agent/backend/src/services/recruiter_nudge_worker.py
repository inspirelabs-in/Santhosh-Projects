"""Pulse proactive nudge worker.

Subscribes to the per-application Redis pubsub channels (already published
by the candidate-side flow + V1 webhooks) and posts ChatGPT-style "system"
rows into the most-recently-active recruiter conversation that has nudges
enabled. Recruiter sees them appear in real-time without asking.

Events surfaced:
  * chat_stage_change         (candidate moved screening -> assignment)
  * chat_message              (candidate replied)
  * assignment_submitted      (candidate finished take-home)
  * voice_call_completed
  * meeting_analyzed
  * stuck_alert               (>48h with no movement)

Per-conversation toggle stored at ``recruiter_conversations.state.nudges_enabled``
(default true).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import desc, select

from src.config import get_settings
from src.db.base import (
    Application,
    Candidate,
    RecruiterConversation,
    Role,
)
from src.db.connection import get_redis, session_scope
from src.db.repositories import recruiter_chat as repo

logger = logging.getLogger(__name__)
_settings = get_settings()


# We listen on a wildcard pattern; Redis pubsub supports psubscribe.
_PATTERN = "hiring-agent:application:*"

# Events worth surfacing.
_RELEVANT = {
    "chat_stage_change",
    "assignment_submitted",
    "voice_call_completed",
    "meeting_analyzed",
    "assignment_artifact_uploaded",
}


async def _format_nudge(
    *,
    application_id: UUID,
    event: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """Build the recruiter-facing nudge text + attachment from a raw event.

    Returns ``None`` if we should ignore (event not relevant or app deleted).
    """
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return None
        cand = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None

    name = (cand.name if cand else None) or "(unnamed candidate)"
    role_title = (role.title if role else None) or "a role"
    base = f"Pulse: {name} ({role_title})"

    if event == "chat_stage_change":
        to = (data or {}).get("to")
        body = f"{base} moved to **{to}**."
    elif event == "assignment_submitted":
        body = f"{base} just submitted their assignment."
    elif event == "voice_call_completed":
        body = f"{base} completed the voice screen."
    elif event == "meeting_analyzed":
        body = f"{base} interview meeting analysed."
    elif event == "assignment_artifact_uploaded":
        body = f"{base} uploaded a new artifact."
    else:
        return None

    return {
        "content": body,
        "attachment": {
            "kind": "nudge-card",
            "data": {
                "application_id": str(application_id),
                "event": event,
                "candidate_name": name,
                "role_title": role_title,
                "ts": datetime.now(tz=UTC).isoformat(),
            },
        },
    }


async def _pick_active_conversation(actor_hash: str | None = None) -> RecruiterConversation | None:
    """Pick the most-recently-updated, non-archived conversation that has
    nudges enabled. If ``actor_hash`` is None, picks across all recruiters
    (used in dev where we don't know who's online).
    """
    cutoff = datetime.now(tz=UTC) - timedelta(hours=24)
    async with session_scope() as session:
        q = (
            select(RecruiterConversation)
            .where(RecruiterConversation.archived.is_(False))
            .where(RecruiterConversation.updated_at >= cutoff)
            .order_by(desc(RecruiterConversation.updated_at))
            .limit(5)
        )
        if actor_hash:
            q = q.where(RecruiterConversation.actor_hash == actor_hash)
        rows = (await session.execute(q)).scalars().all()
    for r in rows:
        if (r.state or {}).get("nudges_enabled", True):
            return r
    return None


async def _push_nudge(conv_id: UUID, body: str, attachment: dict[str, Any]) -> None:
    """Persist a system row + emit an SSE event so any open stream picks it up."""
    async with session_scope() as session:
        await repo.append_message(
            session,
            conversation_id=conv_id,
            role="system",
            content=body,
            attachments=[attachment],
        )
    # Drop a hint into the inbox so the SSE handler emits an attachment event
    # even when no user message is queued. We use a special envelope the
    # runner-bypass branch picks up.
    try:
        client = get_redis()
        await client.publish(
            f"recruiter-chat:{conv_id}:nudge",
            json.dumps({"content": body, "attachment": attachment}),
        )
    except Exception as _pub_err:  # noqa: BLE001
        logger.warning("recruiter nudge pubsub failed (best-effort): %s", _pub_err)


async def _consume(pubsub: aioredis.client.PubSub) -> None:
    async for msg in pubsub.listen():
        if msg.get("type") not in ("pmessage", "message"):
            continue
        channel = (msg.get("channel") or "").split(":")[-1]
        try:
            app_id = UUID(channel)
        except ValueError:
            continue
        try:
            payload = json.loads(msg.get("data") or "{}")
        except json.JSONDecodeError:
            continue
        event = payload.get("event")
        if event not in _RELEVANT:
            continue
        try:
            n = await _format_nudge(
                application_id=app_id, event=event, data=payload.get("data") or {}
            )
        except Exception:  # noqa: BLE001
            logger.exception("format_nudge failed")
            continue
        if n is None:
            continue
        conv = await _pick_active_conversation()
        if conv is None:
            continue  # nobody is listening; the row would be unread noise
        try:
            await _push_nudge(conv.id, n["content"], n["attachment"])
        except Exception:  # noqa: BLE001
            logger.exception("push_nudge failed")


async def run_recruiter_nudge_worker() -> None:
    """Lifespan-managed background task. Resilient to redis flaps."""
    while True:
        try:
            client = aioredis.from_url(_settings.redis_url, decode_responses=True)
            pubsub = client.pubsub()
            await pubsub.psubscribe(_PATTERN)
            logger.info("recruiter nudge worker subscribed to %s", _PATTERN)
            await _consume(pubsub)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            logger.exception("nudge worker crashed; restarting in 5s")
            await asyncio.sleep(5)
