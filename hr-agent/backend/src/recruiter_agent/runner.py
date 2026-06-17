"""Recruiter agent turn loop.

Per inbound user message, runs the OpenAI tool-calling loop:

    user msg -> model decides:
        case A: text reply  -> stream tokens, persist, done
        case B: tool calls  -> emit ``tool_call`` events, dispatch tools,
                                feed results back, repeat (max 3 hops),
                                then stream final text reply

Streaming is via LiteLLM. Tool dispatch is synchronous in the loop.
SSE events emitted to the API layer:

    ``thinking``          {"label": "..."}
    ``tool_call``         {"id": "...", "name": "...", "arguments": {...}}
    ``tool_result``       {"id": "...", "name": "...", "preview": "...", "data": {...}}
    ``token``             {"delta": "..."}
    ``attachment``        {"kind": "candidate-list" | ..., "data": ...}
    ``done``              {"conversation_id": "..."}
    ``error``             {"message": "..."}
"""

from __future__ import annotations

import json
import logging


def _strip_emdash(s: str) -> str:
    """Replace em-dash (U+2014) with a comma + space. Product rule: never emit em-dashes."""
    if not s:
        return s
    # Common patterns: " — " -> ", ". Bare "—" -> ",". en-dash kept.
    return s.replace(" — ", ", ").replace(" —", ",").replace("— ", ", ").replace("—", ",")


def _scrub_emdash_in_args(args: Any) -> Any:
    """Recursively strip em-dashes from any string values inside tool arguments."""
    if isinstance(args, str):
        return _strip_emdash(args)
    if isinstance(args, list):
        return [_scrub_emdash_in_args(v) for v in args]
    if isinstance(args, dict):
        return {k: _scrub_emdash_in_args(v) for k, v in args.items()}
    return args

from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator
from uuid import UUID

import litellm

from src.config import get_settings
from src.db.connection import session_scope
from src.db.repositories import recruiter_chat as repo
from src.db.repositories import recruiter_memory as memory_repo
from src.llm.client import LLMError, assert_model_allowed, pat_sub
from src.recruiter_agent.prompts import RECRUITER_SYSTEM_V1, RECRUITER_SYSTEM_VERSION
from src.recruiter_agent.rbac import can, needs_confirm, required_role
from src.recruiter_agent.schemas import RECRUITER_TOOLS
from src.recruiter_agent.tools import call_tool

logger = logging.getLogger(__name__)
_settings = get_settings()


_COMPANY_NAME = "GrabOn"
_MAX_TOOL_HOPS = 5  # raised so Pulse can chain defaults -> draft -> action
_MAX_HISTORY_TURNS = 20
_CONFIRM_TTL_SECONDS = 600  # 10 min to confirm before pending entry expires


async def _maybe_rename_conversation(conversation_id: UUID) -> None:
    """Generate a 3-5 word title from the first real exchange and persist it.

    Idempotent: bails out if the conv already has a non-default title. Runs
    at the end of EVERY turn (main flow, confirm-pending early return, and
    after confirm-execute) so the sidebar updates regardless of which code
    path produced the assistant reply.
    """
    async with session_scope() as session:
        from src.db.base import RecruiterConversation, RecruiterMessage
        from sqlalchemy import select as _select

        conv = await session.get(RecruiterConversation, conversation_id)
        if conv is None:
            return
        if conv.title and conv.title.strip() and conv.title.strip().lower() != "new chat":
            return  # already named

        # First REAL user message (skip synthetic confirm markers + empties).
        user_rows = (
            await session.execute(
                _select(RecruiterMessage)
                .where(RecruiterMessage.conversation_id == conversation_id)
                .where(RecruiterMessage.role == "user")
                .where(RecruiterMessage.tombstoned.is_(False))
                .order_by(RecruiterMessage.sequence.asc())
                .limit(8)
            )
        ).scalars().all()
        user_msg = ""
        for r in user_rows:
            content = (r.content or "").strip()
            if not content or content.startswith("__pulse_confirm__:"):
                continue
            user_msg = content
            break
        if not user_msg:
            return

        # First non-empty assistant reply for context.
        assistant_rows = (
            await session.execute(
                _select(RecruiterMessage)
                .where(RecruiterMessage.conversation_id == conversation_id)
                .where(RecruiterMessage.role == "assistant")
                .where(RecruiterMessage.tombstoned.is_(False))
                .order_by(RecruiterMessage.sequence.asc())
                .limit(8)
            )
        ).scalars().all()
        assistant_msg = ""
        for r in assistant_rows:
            content = (r.content or "").strip()
            if content:
                assistant_msg = content
                break

    title = await _generate_conversation_title(
        user_message=user_msg, assistant_reply=assistant_msg
    )
    if not title:
        # Heuristic fallback: first 6 words of user message.
        words = user_msg.split()
        title = " ".join(words[:6])[:60]
    if not title:
        return

    async with session_scope() as session:
        conv = await session.get(RecruiterConversation, conversation_id)
        if conv is None:
            return
        conv.title = title[:255]


async def _generate_conversation_title(
    *, user_message: str, assistant_reply: str
) -> str | None:
    """Use the fast model to summarize the first exchange in 3-5 words.

    Returns None on any error so the caller falls back to a heuristic title.
    """
    snippet_user = (user_message or "").strip()[:400]
    snippet_assistant = (assistant_reply or "").strip()[:400]
    if not snippet_user:
        return None
    try:
        resp = await litellm.acompletion(
            model=_settings.llm_model_fast,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the user's intent in 3-5 words for a "
                        "ChatGPT-style chat-list title. Title Case. No quotes. "
                        "No trailing punctuation. <= 40 characters."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"USER: {snippet_user}\nASSISTANT: {snippet_assistant}\n"
                        "Title:"
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=20,
        )
        text = (resp.choices[0].message.content or "").strip()
        text = text.strip("\"'`. ")
        return text[:60] or None
    except Exception:  # noqa: BLE001
        logger.warning("title_gen failed", exc_info=True)
        return None


async def _is_cancelled(conversation_id: UUID) -> bool:
    from src.db.connection import get_redis

    try:
        v = await get_redis().get(f"recruiter-chat:{conversation_id}:cancel")
        return v is not None
    except Exception:  # noqa: BLE001
        return False


async def _clear_cancel(conversation_id: UUID) -> None:
    from src.db.connection import get_redis

    try:
        await get_redis().delete(f"recruiter-chat:{conversation_id}:cancel")
    except Exception:  # noqa: BLE001
        pass


def _propose_preview(tool_name: str, args: dict[str, Any]) -> str:
    """Human-friendly one-liner describing what the agent is about to do.

    Surfaces in the confirm card so the user clicks Confirm with full context.
    """
    a = args or {}
    if tool_name == "trigger_chat_invite":
        return f"Re-send chat invite to application {a.get('application_id')}."
    if tool_name == "create_role":
        return f"Create role '{a.get('title')}' (modality={a.get('screening_modality', 'voice')})."
    if tool_name == "create_role_with_assignment":
        n_problems = len(a.get("problems") or []) or a.get("n_problems") or 2
        return (
            f"Create role '{a.get('title')}' (modality="
            f"{a.get('screening_modality', 'voice')}) + a {n_problems}-problem "
            f"take-home assignment. PDF will be attached to the candidate email."
        )
    if tool_name == "update_role":
        keys = [k for k in a.keys() if k != "role_id"]
        return f"Update role {a.get('role_id')} (fields: {', '.join(keys)})."
    if tool_name == "archive_role":
        return f"Archive role {a.get('role_id')} (status -> closed)."
    if tool_name == "set_role_assignment_brief":
        return f"Replace assignment brief on role {a.get('role_id')}."
    if tool_name == "override_stage":
        return (
            f"Force application {a.get('application_id')} to stage "
            f"'{a.get('to_stage')}'. Reason: {a.get('reason') or '(none)'}"
        )
    if tool_name == "send_custom_email":
        return (
            f"Email candidate of application {a.get('application_id')}. "
            f"Subject: {a.get('subject')}"
        )
    if tool_name == "schedule_interview":
        return (
            f"Schedule interview for application {a.get('application_id')} "
            f"at {a.get('scheduled_at')}."
        )
    if tool_name == "set_panel_member":
        return f"Assign panel member {a.get('panel_member_id')} to {a.get('round')} round of role {a.get('role_id')}."
    if tool_name == "update_setting":
        return f"Set config '{a.get('key')}' = {a.get('value')}."
    return f"{tool_name}({a})"


async def _prerender_for_confirm(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Pre-generate large artifacts so the confirm card shows the full draft.

    Currently handles ``create_role_with_assignment``: drafts the assignment
    BEFORE the user confirms, so the confirm card surfaces all problems +
    brief_md + rubric. The cached draft is stored on ``args`` and reused on
    confirm-execute so we don't regenerate on click.
    """
    if tool_name != "create_role_with_assignment":
        return args
    # Already prerendered (e.g. on a re-issue of the same confirm). Skip.
    if isinstance(args.get("_prerendered_brief"), dict):
        return args
    try:
        from src.agent.generators import gen_assignment

        n = max(1, min(int(args.get("n_problems") or 2), 8))
        from uuid import uuid4 as _uuid4

        # Generate against a synthetic role-scoped uuid (no DB row yet).
        synthetic_id = _uuid4()
        brief = await gen_assignment(
            role_title=args.get("title") or "Role",
            jd_text=args.get("jd_text") or "",
            candidate_profile={},
            screening_answers=None,
            time_budget_hours=int(args.get("time_budget_hours") or 6),
            deadline_days=int(args.get("deadline_days") or 7),
            application_id=synthetic_id,
            candidate_id=synthetic_id,
        )
        payload = brief.model_dump()
        # Pad to n problems if the generator returned fewer.
        if len(payload.get("problems", [])) < n:
            from src.llm.client import get_llm_client
            from src.agent.schemas import AssignmentBriefOut

            client = get_llm_client()
            try:
                extra = await client.complete(
                    prompt=(
                        f"Existing assignment brief for role '{args.get('title')}':\n"
                        f"{json.dumps(payload, ensure_ascii=False)[:6000]}\n\n"
                        f"Add {n - len(payload['problems'])} MORE distinct hard "
                        f"problems matching the same schema. Return the FULL "
                        f"brief with all {n} problems."
                    ),
                    response_model=AssignmentBriefOut,
                    model=client.smart,
                    trace_name="recruiter.assignment_prerender_extend",
                    prompt_version="v2",
                    application_id=synthetic_id,
                    candidate_id=synthetic_id,
                    temperature=0.5,
                    max_tokens=12000,
                )
                payload = extra.parsed.model_dump()
            except Exception as e:  # noqa: BLE001
                logger.warning("prerender_extend failed (non-fatal): %s", e)
        # Surface the draft into args so the confirm card renders it.
        args = dict(args)
        args["problems"] = payload.get("problems") or []
        args["assignment_brief"] = payload.get("brief_md")
        args["evaluation_rubric"] = payload.get("evaluation_rubric")
        args["submission_format"] = payload.get("submission_format")
        args["_prerendered_brief"] = payload  # reused on execute
        return args
    except Exception as e:  # noqa: BLE001
        logger.warning("prerender_for_confirm failed for %s: %s", tool_name, e)
        return args


async def _save_pending_confirm(
    *,
    conversation_id: UUID,
    request_id: str,
    tool: str,
    args: dict[str, Any],
) -> None:
    """Stash a pending tool call in Redis with a TTL. Confirm endpoint pops it."""
    from src.db.connection import get_redis

    client = get_redis()
    key = f"recruiter-confirm:{conversation_id}:{request_id}"
    payload = json.dumps({"tool": tool, "args": args, "request_id": request_id})
    try:
        await client.setex(key, _CONFIRM_TTL_SECONDS, payload)
    except Exception:  # noqa: BLE001
        logger.warning("save_pending_confirm failed", exc_info=True)


async def _consume_pending_confirm(
    conversation_id: UUID, user_message: str
) -> dict[str, Any] | None:
    """If ``user_message`` is the synthetic confirm marker, pop and return.

    The confirm endpoint enqueues a user message of the form
    ``"__pulse_confirm__:<request_id>"`` -- we strip the prefix, look up the
    Redis pending entry, and return its payload.
    """
    if not user_message.startswith("__pulse_confirm__:"):
        return None
    request_id = user_message.split(":", 1)[1].strip()
    if not request_id:
        return None
    from src.db.connection import get_redis

    client = get_redis()
    key = f"recruiter-confirm:{conversation_id}:{request_id}"
    try:
        raw = await client.get(key)
        if raw is None:
            return None
        await client.delete(key)
    except Exception:  # noqa: BLE001
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def _execute_confirmed(
    *,
    conversation_id: UUID,
    actor_hash: str,
    actor_role: str,
    tool_name: str,
    args: dict[str, Any],
    request_id: str,
) -> AsyncIterator[dict[str, Any]]:
    """Run a confirmed tool call. Re-checks RBAC in case roles changed."""
    if not can(actor_role, tool_name):
        yield {
            "type": "tool_result",
            "id": request_id,
            "name": tool_name,
            "preview": _short_preview({"error": "permission_denied"}),
        }
        return

    yield {"type": "thinking", "label": f"Executing {tool_name}"}
    yield {"type": "tool_call", "id": request_id, "name": tool_name, "arguments": args, "confirmed": True}
    result = await call_tool(tool_name, args, actor_hash=actor_hash)
    yield {
        "type": "tool_result",
        "id": request_id,
        "name": tool_name,
        "preview": _short_preview(result),
    }
    attachment = _attachment_for_tool(tool_name, result)
    if attachment is not None:
        yield {
            "type": "attachment",
            "kind": attachment["kind"],
            "data": attachment.get("data") or attachment.get("items"),
            "raw": attachment,
        }

    # Persist a CLEAN assistant tool_calls + tool_result pair so future
    # ``_build_history_for_llm`` reads it as a valid OpenAI conversation.
    # The original proposed-but-not-yet-executed rows stay in the DB for
    # audit, but the history filter drops them on read.
    async with session_scope() as session:
        await repo.append_message(
            session,
            conversation_id=conversation_id,
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": request_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(args)},
                }
            ],
        )
        await repo.append_message(
            session,
            conversation_id=conversation_id,
            role="tool",
            tool_name=tool_name,
            tool_calls=[{"id": request_id}],
            tool_result=result,
            attachments=[attachment] if attachment else None,
        )

    # Brief assistant follow-up -- LLM call.
    nudge = ""
    if tool_name == "create_role_with_assignment" and result.get("ok"):
        nudge = (
            " Then add EXACTLY this question on a new line: 'Want me to draft a "
            "LinkedIn post for this role?'"
        )
    elif tool_name == "create_role" and result.get("ok"):
        nudge = (
            " Then ask: 'Want a 5-problem take-home assignment for this role too?'"
        )
    elif tool_name == "draft_linkedin_post" and result.get("ok"):
        nudge = " Mention they can copy or open LinkedIn from the card."
    follow_up_msgs = [
        {"role": "system", "content": await _system_prompt(actor_hash)},
        {
            "role": "system",
            "content": (
                f"You just executed the confirmed tool '{tool_name}'. The result "
                f"was: {json.dumps(result, default=str)[:600]}. Reply in <= 30 "
                f"words. State the outcome cleanly. Do not make further tool calls."
                + nudge
            ),
        },
    ]
    try:
        stream = await litellm.acompletion(
            model=_settings.llm_model_fast,
            messages=follow_up_msgs,
            temperature=0.2,
            max_tokens=200,
            stream=True,
        )
        full = ""
        async for chunk in stream:
            ch = (getattr(chunk, "choices", None) or [None])[0]
            delta = getattr(ch, "delta", None) if ch else None
            content = getattr(delta, "content", None) if delta else None
            if content:
                content = _strip_emdash(content)
                full += content
                yield {"type": "token", "delta": content}
        full = _strip_emdash(full)
        async with session_scope() as session:
            await repo.append_message(
                session,
                conversation_id=conversation_id,
                role="assistant",
                content=full,
                model=_settings.llm_model_fast,
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("post-confirm follow-up failed")
        yield {"type": "error", "message": f"follow_up_failed: {e}"}

    await _maybe_rename_conversation(conversation_id)
    yield {"type": "done", "conversation_id": str(conversation_id)}


async def _recent_events(limit: int = 10) -> str:
    """Tail of audit_log + recent stage transitions, rendered for the prompt."""
    from sqlalchemy import desc as _desc, select as _select
    from src.db.base import AuditLog

    cutoff = datetime.now(tz=UTC) - timedelta(minutes=15)
    try:
        async with session_scope() as session:
            rows = (
                await session.execute(
                    _select(AuditLog)
                    .where(AuditLog.created_at >= cutoff)
                    .order_by(_desc(AuditLog.created_at))
                    .limit(limit)
                )
            ).scalars().all()
    except Exception:  # noqa: BLE001
        return ""
    if not rows:
        return ""
    lines = [
        f"- {(r.created_at or datetime.now(tz=UTC)).strftime('%H:%M')} {r.action} (app={r.application_id})"
        for r in rows
    ]
    return "\n".join(lines[:limit])


async def _system_prompt(actor_hash: str | None = None) -> str:
    base = RECRUITER_SYSTEM_V1.format(
        company_name=_COMPANY_NAME,
        today=datetime.now(tz=UTC).strftime("%Y-%m-%d"),
    )
    if actor_hash:
        try:
            async with session_scope() as session:
                snap = await memory_repo.snapshot_for_prompt(
                    session, actor_hash=actor_hash, max_chars=1200
                )
            if snap:
                base += (
                    "\n\nRecruiter long-term memory (refer when relevant; do "
                    "not repeat back unless asked):\n" + snap
                )
        except Exception:  # noqa: BLE001
            logger.warning("memory snapshot failed", exc_info=True)

    # Live pipeline awareness: tail of recent audit-log rows. Cheap, gives
    # Pulse situational awareness of what just happened in the system.
    try:
        events = await _recent_events(limit=8)
        if events:
            base += "\n\nRecent pipeline events (last 15 min):\n" + events
    except Exception:  # noqa: BLE001
        logger.warning("recent_events failed", exc_info=True)
    return base


def _attachment_for_tool(name: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """Map tool results to UI cards. None -> tool result rendered as raw JSON.

    Keep these stable; the frontend has matching renderers per ``kind``.
    """
    if "error" in result:
        return None
    if name == "list_candidates" and isinstance(result.get("items"), list):
        return {"kind": "candidate-list", "items": result["items"]}
    if name == "list_roles" and isinstance(result.get("items"), list):
        return {"kind": "role-list", "items": result["items"]}
    if name == "stuck_applications" and isinstance(result.get("items"), list):
        return {"kind": "stuck-list", "items": result["items"]}
    if name == "audit_tail" and isinstance(result.get("items"), list):
        return {"kind": "audit-list", "items": result["items"]}
    if name == "pipeline_metrics":
        return {"kind": "metrics", "data": result}
    if name == "get_candidate":
        return {"kind": "candidate-detail", "data": result}
    if name == "trigger_chat_invite":
        return {"kind": "action-result", "data": result}
    if name == "draft_linkedin_post" and result.get("ok"):
        return {"kind": "linkedin-post", "data": result}
    if name == "create_role_with_assignment" and result.get("ok"):
        return {"kind": "role-created", "data": result}
    return None


def _short_preview(result: dict[str, Any], limit: int = 200) -> str:
    try:
        s = json.dumps(result, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return ""
    return s if len(s) <= limit else s[:limit] + "..."


def _is_pending_confirm_assistant(m: Any) -> bool:
    """Assistant row whose tool_calls are ALL the confirm-only proposals.

    These rows have ``requires_confirmation=True`` flags inside tool_calls,
    and their corresponding ``tool`` rows hold ``{"status": "awaiting_confirmation"}``.
    Including them in OpenAI history without a real tool-result message
    triggers ``messages with role 'tool' must follow tool_calls`` errors,
    so we drop them on read. The DB still has them for audit.
    """
    tcs = m.tool_calls or []
    if not tcs:
        return False
    return all(
        bool((tc or {}).get("requires_confirmation"))
        or bool((tc or {}).get("requiresConfirmation"))
        for tc in tcs
    )


def _is_awaiting_confirm_tool(m: Any) -> bool:
    if m.role != "tool":
        return False
    tr = m.tool_result or {}
    return isinstance(tr, dict) and tr.get("status") == "awaiting_confirmation"


def _is_synthetic_confirm_user(m: Any) -> bool:
    """Drop the ``__pulse_confirm__:<id>`` user marker from LLM history -- it's
    a runner-internal trigger, not something the model should see."""
    return (
        m.role == "user"
        and isinstance(m.content, str)
        and m.content.startswith("__pulse_confirm__:")
    )


async def _build_history_for_llm(
    session, conversation_id: UUID
) -> list[dict[str, Any]]:
    """Convert persisted ``recruiter_messages`` rows into OpenAI chat format.

    Bulletproof tool-call pairing. OpenAI rejects ANY assistant message
    whose tool_calls aren't all answered by matching tool messages and ANY
    tool message that doesn't follow a valid tool_calls. We do a two-pass
    walk:

      Pass 1: collect every tool_call_id that has a tool-response row.
      Pass 2: emit assistant rows with only the tool_calls that have
              responses; emit tool rows only after their assistant turn.
              If an assistant ends up with no content + no valid tool_calls
              after filtering, drop the entire row.
    """
    raw = await repo.list_messages(session, conversation_id)
    raw = [m for m in raw if not _is_synthetic_confirm_user(m)]
    raw = [m for m in raw if not _is_pending_confirm_assistant(m)]
    raw = [m for m in raw if not _is_awaiting_confirm_tool(m)]
    raw = raw[-_MAX_HISTORY_TURNS * 4 :]

    # Pass 0: same tool_call_id can appear on multiple assistant rows
    # because the confirm-flow persists a "proposal" assistant + tool row
    # using id X, then ``_execute_confirmed`` re-uses id X when writing the
    # clean executed pair. OpenAI rejects duplicate-id messages and views
    # the first claim as orphaned. Keep only the LATEST assistant index that
    # claimed each id; older claims have their tool_calls stripped below.
    last_owner_idx: dict[str, int] = {}
    for i, m in enumerate(raw):
        if m.role == "assistant":
            for tc in m.tool_calls or []:
                cid = (tc or {}).get("id") if isinstance(tc, dict) else None
                if isinstance(cid, str) and cid:
                    last_owner_idx[cid] = i

    # Pass 1: find every tool_call_id that has a tool response (tool row).
    # Same dedupe -- if multiple tool rows reference the same id, the LATEST
    # one wins. We track index too so a tool row paired with a stripped
    # owner (older assistant) is dropped.
    responded: set[str] = set()
    last_response_idx: dict[str, int] = {}
    for i, m in enumerate(raw):
        if m.role == "tool":
            cid = (m.tool_calls or [{}])[0].get("id") if m.tool_calls else None
            if isinstance(cid, str) and cid:
                responded.add(cid)
                last_response_idx[cid] = i

    # Pass 2: emit OpenAI-shaped messages.
    emitted_tc_ids: set[str] = set()
    out: list[dict[str, Any]] = []
    for i, m in enumerate(raw):
        if m.role == "user" and m.content:
            out.append({"role": "user", "content": m.content})

        elif m.role == "assistant":
            clean_tcs: list[dict[str, Any]] = []
            for tc in m.tool_calls or []:
                if not isinstance(tc, dict):
                    continue
                cid = tc.get("id")
                fn = tc.get("function") or {}
                if not (isinstance(cid, str) and cid and fn.get("name")):
                    continue
                if cid not in responded:
                    # Orphan tool_call -- never executed (or response lost).
                    continue
                # Only the LATEST assistant claim wins; older duplicates get
                # their tool_calls stripped (otherwise OpenAI sees the first
                # claim as orphaned because the matching tool message lives
                # under the second claim).
                if last_owner_idx.get(cid) != i:
                    continue
                emitted_tc_ids.add(cid)
                clean_tcs.append(
                    {
                        "id": cid,
                        "type": "function",
                        "function": {
                            "name": fn.get("name"),
                            "arguments": fn.get("arguments") or "{}",
                        },
                    }
                )
            content = (m.content or "").strip()
            # Drop assistant rows that would be empty + tool-call-less.
            if not clean_tcs and not content:
                continue
            entry: dict[str, Any] = {"role": "assistant", "content": m.content or ""}
            if clean_tcs:
                entry["tool_calls"] = clean_tcs
            out.append(entry)

        elif m.role == "tool":
            cid = (m.tool_calls or [{}])[0].get("id") if m.tool_calls else None
            if not (isinstance(cid, str) and cid):
                continue
            if cid not in emitted_tc_ids:
                # Owner assistant was stripped above; skip this tool row too.
                continue
            # Only the LATEST tool response wins; older duplicates dropped.
            if last_response_idx.get(cid) != i:
                continue
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": cid,
                    "name": m.tool_name,
                    "content": json.dumps(m.tool_result, default=str)
                    if m.tool_result is not None
                    else "",
                }
            )
        # Skip "system" rows; system prompt is rebuilt each turn.

    # Final safety pass: ensure every assistant.tool_calls.id has a
    # subsequent tool message in ``out``. If any orphan slipped through,
    # strip the whole tool_calls field on that assistant -- worst case the
    # model re-issues the call.
    pending: dict[int, set[str]] = {}
    for i, msg in enumerate(out):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            pending[i] = {tc["id"] for tc in msg["tool_calls"]}
        elif msg.get("role") == "tool":
            tcid = msg.get("tool_call_id")
            for k in pending:
                pending[k].discard(tcid)
    for i, missing in list(pending.items()):
        if missing:
            tcs = out[i].get("tool_calls") or []
            kept = [tc for tc in tcs if tc["id"] not in missing]
            if kept:
                out[i]["tool_calls"] = kept
            else:
                out[i].pop("tool_calls", None)
            # If the assistant becomes empty, mark it for removal below.
    out = [
        msg
        for msg in out
        if not (
            msg.get("role") == "assistant"
            and not (msg.get("content") or "").strip()
            and not msg.get("tool_calls")
        )
    ]

    return out


async def run_recruiter_turn(
    *,
    conversation_id: UUID,
    user_message: str,
) -> AsyncIterator[dict[str, Any]]:
    """One recruiter turn. Persists user message, runs tool loop, streams reply."""
    # Persist inbound user message first.
    async with session_scope() as session:
        conv = await session.get(
            __import__("src.db.base", fromlist=["RecruiterConversation"]).RecruiterConversation,
            conversation_id,
        )
        if conv is None:
            yield {"type": "error", "message": "conversation_not_found"}
            return
        await repo.append_message(
            session,
            conversation_id=conversation_id,
            role="user",
            content=user_message.strip(),
        )

    # If this is a confirmed-tool re-entry, short-circuit to execution.
    confirmed = await _consume_pending_confirm(conversation_id, user_message.strip())
    if confirmed is not None:
        async for ev in _execute_confirmed(
            conversation_id=conversation_id,
            actor_hash=conv.actor_hash,
            actor_role=conv.actor_role,
            tool_name=confirmed["tool"],
            args=confirmed["args"],
            request_id=confirmed["request_id"],
        ):
            yield ev
        return

    await _clear_cancel(conversation_id)
    yield {"type": "thinking", "label": "Reading your request"}

    system_msg = {"role": "system", "content": await _system_prompt(conv.actor_hash)}
    actor_hash = conv.actor_hash
    actor_role = conv.actor_role

    # ---- Tool-call loop ----
    final_text = ""
    final_usage: dict[str, Any] = {}
    for hop in range(_MAX_TOOL_HOPS + 1):
        async with session_scope() as session:
            history = await _build_history_for_llm(session, conversation_id)
        messages = [system_msg, *history]

        model = _settings.llm_model_fast
        assert_model_allowed(model)

        # Stream-first: single streaming call detects both tool_calls and
        # text replies. Eliminates the old double-call pattern that caused
        # 2x latency on simple messages like "hi".
        try:
            stream = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=RECRUITER_TOOLS,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=900,
                stream=True,
                stream_options={"include_usage": True},
            )
        except litellm.exceptions.AuthenticationError as e:
            yield {"type": "error", "message": f"LLM auth failure: {pat_sub(str(e))}"}
            return
        except litellm.exceptions.BadRequestError as e:
            err = pat_sub(str(e))
            if "tool_call" in err.lower() or "tool_calls" in err.lower():
                logger.warning("self-healing tool_calls history error: %s", err)
                stripped = [
                    {"role": m.get("role"), "content": m.get("content") or ""}
                    for m in messages
                    if m.get("role") in ("system", "user", "assistant")
                    and not m.get("tool_calls")
                    and (m.get("content") or "").strip()
                ]
                stripped = [m for m in stripped if m.get("role") != "tool"]
                try:
                    stream = await litellm.acompletion(
                        model=model,
                        messages=stripped,
                        tools=RECRUITER_TOOLS,
                        tool_choice="auto",
                        temperature=0.2,
                        max_tokens=900,
                        stream=True,
                        stream_options={"include_usage": True},
                    )
                except Exception as e2:  # noqa: BLE001
                    yield {"type": "error", "message": f"agent_recovery_failed: {pat_sub(str(e2))}"}
                    return
            else:
                yield {"type": "error", "message": f"LLM bad request: {err}"}
                return
        except Exception as e:  # noqa: BLE001
            logger.exception("recruiter llm call crashed")
            yield {"type": "error", "message": f"agent_error: {e}"}
            return

        # Consume the stream: accumulate text tokens AND tool_call deltas.
        full_text = ""
        accumulated_tool_calls: dict[int, dict[str, Any]] = {}
        usage = {"model": model}
        was_cancelled = False
        async for chunk in stream:
            if await _is_cancelled(conversation_id):
                yield {"type": "cancelled"}
                was_cancelled = True
                break
            ch = (getattr(chunk, "choices", None) or [None])[0]
            delta = getattr(ch, "delta", None) if ch else None
            if delta is None:
                u = getattr(chunk, "usage", None)
                if u is not None:
                    usage["input_tokens"] = int(getattr(u, "prompt_tokens", 0) or 0)
                    usage["output_tokens"] = int(getattr(u, "completion_tokens", 0) or 0)
                continue
            # Text content
            content = getattr(delta, "content", None)
            if content:
                content = _strip_emdash(content)
                full_text += content
                yield {"type": "token", "delta": content}
            # Tool call deltas
            tc_deltas = getattr(delta, "tool_calls", None) or []
            for tc_delta in tc_deltas:
                idx = getattr(tc_delta, "index", None)
                if idx is None:
                    idx = 0
                if idx not in accumulated_tool_calls:
                    accumulated_tool_calls[idx] = {
                        "id": getattr(tc_delta, "id", None) or "",
                        "function_name": "",
                        "function_args": "",
                    }
                entry = accumulated_tool_calls[idx]
                if getattr(tc_delta, "id", None):
                    entry["id"] = tc_delta.id
                fn = getattr(tc_delta, "function", None)
                if fn:
                    if getattr(fn, "name", None):
                        entry["function_name"] += fn.name
                    if getattr(fn, "arguments", None):
                        entry["function_args"] += fn.arguments
            u = getattr(chunk, "usage", None)
            if u is not None:
                usage["input_tokens"] = int(getattr(u, "prompt_tokens", 0) or 0)
                usage["output_tokens"] = int(getattr(u, "completion_tokens", 0) or 0)

        if was_cancelled:
            final_text = ""
            final_usage = usage
            return

        # Build tool_calls list from accumulated deltas.
        tool_calls = []
        for idx in sorted(accumulated_tool_calls.keys()):
            entry = accumulated_tool_calls[idx]
            if entry["id"] and entry["function_name"]:
                tool_calls.append(type("TC", (), {
                    "id": entry["id"],
                    "function": type("Fn", (), {
                        "name": entry["function_name"],
                        "arguments": entry["function_args"],
                    })(),
                })())

        # ---- No tool calls: we already streamed the text ----
        if not tool_calls:
            if hop == 0 and not full_text.strip():
                yield {"type": "error", "message": "empty_response"}
                return
            final_text = full_text
            final_usage = usage
            break

        # ---- Tool calls: dispatch each, feed results back, loop ----
        # Persist the assistant's tool-call message first so history rebuilds
        # cleanly on next iteration.
        serialized_calls: list[dict[str, Any]] = []
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            args = _scrub_emdash_in_args(args)
            # Re-serialize so the raw JSON we persist matches the scrubbed
            # parsed args. Without this, history reconstruction reads the
            # unscrubbed arguments and the tool dispatcher reads scrubbed --
            # silent drift between two layers.
            scrubbed_args_json = json.dumps(args, ensure_ascii=False)
            serialized_calls.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": scrubbed_args_json},
                    "_args": args,
                }
            )

        async with session_scope() as session:
            await repo.append_message(
                session,
                conversation_id=conversation_id,
                role="assistant",
                content=full_text or "",
                tool_calls=[
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["function"]["name"], "arguments": c["function"]["arguments"]},
                    }
                    for c in serialized_calls
                ],
                model=model,
            )

        # Run each tool, emit events, persist result. RBAC + confirm-gating
        # happen here.
        confirm_pending = False
        for c in serialized_calls:
            name = c["function"]["name"]
            args = c["_args"]

            # ---- RBAC ----
            if not can(actor_role, name):
                refusal = {
                    "error": "permission_denied",
                    "needs_role": required_role(name),
                }
                yield {"type": "tool_call", "id": c["id"], "name": name, "arguments": args}
                yield {"type": "tool_result", "id": c["id"], "name": name, "preview": _short_preview(refusal)}
                async with session_scope() as session:
                    await repo.append_message(
                        session,
                        conversation_id=conversation_id,
                        role="tool",
                        tool_name=name,
                        tool_calls=[{"id": c["id"]}],
                        tool_result=refusal,
                    )
                continue

            # ---- Confirm gate ----
            if needs_confirm(name):
                request_id = c["id"]
                # For combo tools that generate large artifacts (assignment
                # brief, etc.), pre-render the artifact so the confirm card
                # shows the FULL draft to the user before they confirm. The
                # tool then receives the cached payload on confirm-execute
                # and skips regeneration.
                if name == "create_role_with_assignment":
                    yield {
                        "type": "thinking",
                        "label": "Drafting the role + 5-problem assignment...",
                    }
                # Wrap in a hard timeout so a slow LLM never freezes the UI.
                # If prerender stalls, show the confirm card without the
                # generated problems; the execute path will still produce a
                # full brief in the background.
                import asyncio as _aio
                try:
                    args = await _aio.wait_for(
                        _prerender_for_confirm(name, args),
                        timeout=45.0,
                    )
                except _aio.TimeoutError:
                    logger.warning(
                        "prerender for %s timed out after 45s; "
                        "showing confirm card without draft",
                        name,
                    )
                preview = _propose_preview(name, args)
                attachment = {
                    "kind": "confirm-card",
                    "data": {
                        "request_id": request_id,
                        "tool": name,
                        "args": args,
                        "preview": preview,
                    },
                }
                # Persist pending confirm so /confirm can retrieve it.
                await _save_pending_confirm(
                    conversation_id=conversation_id,
                    request_id=request_id,
                    tool=name,
                    args=args,
                )
                yield {"type": "tool_call", "id": request_id, "name": name, "arguments": args, "requires_confirmation": True}
                yield {"type": "attachment", "kind": "confirm-card", "data": attachment["data"], "raw": attachment}
                async with session_scope() as session:
                    await repo.append_message(
                        session,
                        conversation_id=conversation_id,
                        role="tool",
                        tool_name=name,
                        tool_calls=[{"id": request_id, "requires_confirmation": True}],
                        tool_result={"status": "awaiting_confirmation", "request_id": request_id},
                        attachments=[attachment],
                    )
                # Halt the loop; the user will reply via /confirm or via a
                # natural-language confirmation we don't try to parse here.
                confirm_pending = True
                break

            # ---- Direct execution ----
            yield {"type": "tool_call", "id": c["id"], "name": name, "arguments": args}
            result = await call_tool(name, args, actor_hash=actor_hash)
            yield {
                "type": "tool_result",
                "id": c["id"],
                "name": name,
                "preview": _short_preview(result),
            }
            attachment = _attachment_for_tool(name, result)
            if attachment is not None:
                yield {
                    "type": "attachment",
                    "kind": attachment["kind"],
                    "data": attachment.get("data") or attachment.get("items"),
                    "raw": attachment,
                }

            async with session_scope() as session:
                await repo.append_message(
                    session,
                    conversation_id=conversation_id,
                    role="tool",
                    tool_name=name,
                    tool_calls=[{"id": c["id"]}],
                    tool_result=result,
                    attachments=[attachment] if attachment else None,
                )

        if confirm_pending:
            await _maybe_rename_conversation(conversation_id)
            yield {
                "type": "done",
                "conversation_id": str(conversation_id),
                "awaiting_confirmation": True,
            }
            return

        # If we hit hop limit, force a summarising final pass next iter.
        if hop == _MAX_TOOL_HOPS - 1:
            async with session_scope() as session:
                await repo.append_message(
                    session,
                    conversation_id=conversation_id,
                    role="system",
                    content="Tool budget reached. Reply now without further tool calls.",
                )

    # Persist final assistant text.
    async with session_scope() as session:
        await repo.append_message(
            session,
            conversation_id=conversation_id,
            role="assistant",
            content=final_text,
            model=final_usage.get("model"),
            input_tokens=final_usage.get("input_tokens"),
            output_tokens=final_usage.get("output_tokens"),
        )
        # Title generation moved into ``_maybe_rename_conversation`` so it
        # runs in confirm-pending + confirm-execute paths too.
        pass

    await _maybe_rename_conversation(conversation_id)
    yield {"type": "done", "conversation_id": str(conversation_id), "prompt_version": RECRUITER_SYSTEM_VERSION}
