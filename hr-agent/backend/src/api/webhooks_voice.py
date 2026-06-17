"""ElevenLabs Conversational AI webhooks.

ElevenLabs posts ``post_call_transcription`` once a conversation ends. The
payload carries the full transcript, analysis block, recording URL, and
metadata. We translate that into our internal ``voice_calls`` lifecycle:

  conversation in_progress -> VoiceCallStatus.IN_PROGRESS / stage VOICE_SCREEN_IN_PROGRESS
  conversation done        -> save artifacts + enqueue evaluator
  conversation failed      -> stage NEEDS_HR_REVIEW

ElevenLabs also fires ``post_call_audio`` separately when the audio file is
ready. We accept either event on the same endpoint.

Idempotency: re-deliveries are deduped by the ``transcript_r2_key`` already
being set on the row.
"""

from __future__ import annotations

import hmac
import logging
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.activities.v1_evaluate_voice_call import evaluate_voice_call
from src.activities.v1_dispatch_voice_call import dispatch_voice_call
from src.activities.v1_voice_screening import dispatch_voice_screening
from src.config import get_settings
from src.db.base import VoiceCall
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.db.repositories.voice_call import (
    get_by_provider_id,
    mark_failed,
    mark_in_progress,
    save_call_completion,
    save_callback_request,
)
from src.models.v1 import EmotionFeatures, PipelineStage, VoiceCallStatus
from src.services.emotion_client import analyze_recording
from src.services.events import publish_event
from src.services.file_storage import presigned_get_url, upload_blob
from src.services.queue import enqueue
from src.services.typed_event_bus import EventType
from src.services.typed_event_bus import publish_event as publish_supervisor_event
from src.services.voice_window import clamp_to_call_window

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/voice", tags=["voice-webhooks"])


# ---------------------------------------------------------------------------
# ElevenLabs payload (post_call_transcription)
# ---------------------------------------------------------------------------


class ElTranscriptTurn(BaseModel):
    role: str  # "agent" | "user"
    message: str | None = None
    time_in_call_secs: float | None = None


class ElMetadata(BaseModel):
    call_duration_secs: float | None = None
    cost: dict[str, Any] | None = None


class ElData(BaseModel):
    agent_id: str | None = None
    conversation_id: str
    status: str | None = None  # done | failed | in_progress
    transcript: list[ElTranscriptTurn] = Field(default_factory=list)
    metadata: ElMetadata | None = None
    analysis: dict[str, Any] | None = None
    has_audio: bool | None = None


class ElPayload(BaseModel):
    type: str  # post_call_transcription | post_call_audio | conversation_started
    event_timestamp: int | None = None
    data: ElData


# ---------------------------------------------------------------------------
# Signature verification (ElevenLabs uses HMAC-SHA256)
# ---------------------------------------------------------------------------


_SIG_MAX_AGE_SECONDS = 300  # 5 min replay window


def _verify_signature(raw: bytes, signature: str | None) -> None:
    """Verify ElevenLabs HMAC signature with freshness + prod-strict mode.

    ElevenLabs signs ``f"{timestamp}.{body}"`` with HMAC-SHA256 and sends the
    header as ``t=<unix_ts>,v0=<hex>``. We:

      1. Require the timestamped ``t=...,v0=...`` form in production
         (rejects bare hex / legacy senders).
      2. Reject timestamps older than 5 minutes (replay-attack window).
      3. Constant-time compare on the signature hex.
    """
    settings = get_settings()
    secret = settings.elevenlabs_webhook_secret
    if not secret:
        logger.warning("elevenlabs_webhook_secret not configured — rejecting unsigned webhook")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Webhook secret not configured")
    if not signature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing signature")

    is_prod = (settings.app_env or "").lower() in {"production", "prod"}

    ts_match = re.search(r"t=([0-9]+)", signature)
    sig_match = re.search(r"v0=([0-9a-f]+)", signature, flags=re.IGNORECASE)

    if not sig_match:
        # Bare-hex legacy fallback. Hard-disabled in production -- the prod
        # webhook MUST use the timestamped ElevenLabs format. Dev/test still
        # accepts it so unit tests posting raw hex keep working.
        if is_prod:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Invalid signature format (timestamped v0= required)",
            )
        expected = hmac.new(secret.encode("utf-8"), raw, sha256).hexdigest()
        if not hmac.compare_digest(expected, signature.strip().lower()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
        return

    # Timestamp freshness check (replay prevention). The exception is when
    # ts is missing entirely AND we're outside prod: keep accepting for
    # legacy tests, but log loudly.
    if ts_match:
        try:
            sig_ts = int(ts_match.group(1))
        except ValueError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad timestamp")
        import time as _time

        skew = abs(int(_time.time()) - sig_ts)
        if skew > _SIG_MAX_AGE_SECONDS:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                f"Signature timestamp too old ({skew}s; max {_SIG_MAX_AGE_SECONDS}s)",
            )
    elif is_prod:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing timestamp in signature header",
        )

    sig_hex = sig_match.group(1).lower()
    if ts_match:
        signed_payload = f"{ts_match.group(1)}.".encode("utf-8") + raw
    else:
        signed_payload = raw
    expected = hmac.new(secret.encode("utf-8"), signed_payload, sha256).hexdigest()
    if not hmac.compare_digest(expected, sig_hex):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")


# ---------------------------------------------------------------------------
# Storage + helpers
# ---------------------------------------------------------------------------


def _transcript_text(turns: list[ElTranscriptTurn]) -> str:
    out = []
    for t in turns:
        speaker = "agent" if t.role == "agent" else "candidate"
        out.append(f"[{speaker}] {(t.message or '').strip()}")
    return "\n".join(out)


def _extract_callback(turns: list[ElTranscriptTurn]) -> tuple[datetime | None, str | None]:
    """Look for the agent-emitted ``CALLBACK_AT=<iso>; REASON=<text>`` marker.

    The agent is instructed to return a tz-aware ISO 8601 timestamp. If the
    candidate provides a wall-clock time without timezone we assume IST and
    convert to UTC so the Arq deferred job fires at the right moment.
    """
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    for t in turns:
        if t.role != "agent" or not t.message:
            continue
        m = re.search(r"CALLBACK_AT=([^;\s]+)(?:;\s*REASON=([^\n]*))?", t.message)
        if not m:
            continue
        try:
            dt = datetime.fromisoformat(m.group(1).strip())
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ist)
        dt = dt.astimezone(UTC)
        reason = (m.group(2) or "").strip() or None
        return dt, reason
    return None, None


def _extract_callback_from_analysis(
    analysis: dict[str, Any] | None,
) -> tuple[datetime | None, str | None]:
    """Read ElevenLabs Data Collection results from the analysis block.

    Configure your agent in the ElevenLabs dashboard with these data
    collection fields so the agent extracts them silently after the call:

      * ``callback_at_iso`` (string, ISO 8601)  -- requested callback time
      * ``callback_reason`` (string)            -- candidate's reason
      * ``outcome`` (string)                    -- one of: completed,
        callback_requested, declined, no_answer

    The webhook trusts these over any spoken marker.
    """
    from zoneinfo import ZoneInfo

    if not analysis:
        return None, None
    results = analysis.get("data_collection_results")
    if not isinstance(results, dict):
        return None, None

    def _val(key: str) -> str | None:
        node = results.get(key)
        if isinstance(node, dict):
            v = node.get("value")
            if isinstance(v, str) and v.strip():
                return v.strip()
        if isinstance(node, str) and node.strip():
            return node.strip()
        return None

    raw_iso = _val("callback_at_iso") or _val("callback_at")
    if not raw_iso:
        return None, _val("callback_reason")
    try:
        dt = datetime.fromisoformat(raw_iso)
    except ValueError:
        return None, _val("callback_reason")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    return dt.astimezone(UTC), _val("callback_reason")


_TIME_RE = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?:[:\.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b",
    re.IGNORECASE,
)
_DAY_RE = re.compile(
    r"\b(today|tonight|tomorrow|day after tomorrow|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)


def _extract_callback_natural(
    turns: list[ElTranscriptTurn],
) -> tuple[datetime | None, str | None]:
    """Last-resort: parse a callback time from natural agent confirmation.

    Looks at the AGENT's last few turns for a sentence like
    "I will call you back at 3:30 PM today" and converts it to a tz-aware
    UTC datetime. Returns (None, None) if no time is detectable.
    """
    from datetime import timedelta as _td
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(UTC).astimezone(ist)

    agent_msgs = [t.message for t in turns if t.role == "agent" and t.message]
    if not agent_msgs:
        return None, None

    # Prefer the LAST agent message that mentions both a time and a day-word
    # (or just a time -- assume today).
    for msg in reversed(agent_msgs):
        time_m = _TIME_RE.search(msg)
        if not time_m:
            continue
        hour = int(time_m.group(1))
        minute = int(time_m.group(2) or 0)
        ampm = time_m.group(3).lower().replace(".", "")
        if ampm.startswith("p") and hour < 12:
            hour += 12
        if ampm.startswith("a") and hour == 12:
            hour = 0

        day_m = _DAY_RE.search(msg)
        target = now_ist.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if day_m:
            word = day_m.group(1).lower()
            if word in ("tomorrow", "day after tomorrow"):
                target = target + _td(days=2 if word == "day after tomorrow" else 1)
            elif word not in ("today", "tonight"):
                weekdays = [
                    "monday", "tuesday", "wednesday", "thursday",
                    "friday", "saturday", "sunday",
                ]
                target_wd = weekdays.index(word)
                delta = (target_wd - now_ist.weekday()) % 7 or 7
                target = target + _td(days=delta)
        elif target <= now_ist:
            # No day word and the time already passed today -> assume tomorrow.
            target = target + _td(days=1)

        return target.astimezone(UTC), msg.strip()[:200]
    return None, None


def _extract_confirmation(
    turns: list[ElTranscriptTurn],
) -> tuple[str | None, datetime | None]:
    """Parse the agent's ``CONFIRM=...`` marker emitted on confirmation calls.

    Returns (decision, requested_at) where decision is one of:
        "yes" | "no" | "reschedule" | None
    and ``requested_at`` is set only when the candidate proposes a different time.
    """

    for t in turns:
        if t.role != "agent" or not t.message:
            continue
        m = re.search(r"CONFIRM=(yes|no|reschedule)", t.message, re.IGNORECASE)
        if not m:
            continue
        decision = m.group(1).lower()
        requested_at: datetime | None = None
        rs = re.search(r"REQUESTED_AT=([^;\s]+)", t.message)
        if rs:
            try:
                requested_at = datetime.fromisoformat(rs.group(1).strip())
            except ValueError:
                requested_at = None
        return decision, requested_at
    return None, None


def _zip_answers(
    questions: list[dict[str, Any]] | dict | None,
    turns: list[ElTranscriptTurn],
) -> list[dict[str, Any]]:
    """Map each question to the candidate utterances that followed it.

    The naive ``user_turns[idx]`` approach mis-aligned everything because the
    candidate's first ``"Yes"`` (consent to proceed) consumed slot q1 and
    every follow-up probe shifted the rest. We now anchor each question to
    the agent turn that actually contains the question text, then collect all
    user turns up to the next question or end-of-call. Probe responses get
    appended to the parent question's answer instead of bleeding into q2.
    """
    qs = questions if isinstance(questions, list) else []
    if not qs:
        return []

    def _normalise(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    # Index turns and find each question's anchor agent-turn.
    # We accept a partial-match if at least 12 chars from the question text
    # appear in the agent's utterance (handles minor TTS rephrasing).
    anchors: list[int | None] = []
    used: set[int] = set()
    for q in qs:
        q_norm = _normalise(q.get("question", ""))
        anchor: int | None = None
        if q_norm:
            # First try a strong substring match (>=18 chars).
            probe = q_norm[:60]
            for i, t in enumerate(turns):
                if i in used or t.role != "agent" or not t.message:
                    continue
                a_norm = _normalise(t.message)
                if probe and probe in a_norm:
                    anchor = i
                    break
            # Fallback: first agent turn containing 5+ shared significant words.
            if anchor is None:
                q_tokens = {w for w in q_norm.split() if len(w) >= 4}
                for i, t in enumerate(turns):
                    if i in used or t.role != "agent" or not t.message:
                        continue
                    a_tokens = set(_normalise(t.message).split())
                    if len(q_tokens & a_tokens) >= 5:
                        anchor = i
                        break
        anchors.append(anchor)
        if anchor is not None:
            used.add(anchor)

    # Compute answer windows: from anchor+1 up to the next anchor (or end).
    sorted_anchors = sorted([(a, idx) for idx, a in enumerate(anchors) if a is not None])
    out: list[dict[str, Any]] = [None] * len(qs)  # type: ignore[list-item]
    for k, (start, q_idx) in enumerate(sorted_anchors):
        end = sorted_anchors[k + 1][0] if k + 1 < len(sorted_anchors) else len(turns)
        user_msgs: list[str] = []
        first_ts: float | None = None
        last_ts: float | None = None
        for t in turns[start + 1 : end]:
            if t.role != "user" or not t.message:
                continue
            user_msgs.append(t.message.strip())
            if first_ts is None:
                first_ts = t.time_in_call_secs
            last_ts = t.time_in_call_secs
        q = qs[q_idx]
        dur = (
            (last_ts - first_ts)
            if (first_ts is not None and last_ts is not None and last_ts >= first_ts)
            else None
        )
        out[q_idx] = {
            "question_id": q.get("id") or f"q{q_idx + 1}",
            "question": q.get("question", ""),
            "answer_transcript": " ".join(user_msgs).strip(),
            "duration_sec": dur,
        }

    # Fill any unmatched questions with empty answers so length stays stable.
    for q_idx, q in enumerate(qs):
        if out[q_idx] is None:
            out[q_idx] = {
                "question_id": q.get("id") or f"q{q_idx + 1}",
                "question": q.get("question", ""),
                "answer_transcript": "",
                "duration_sec": None,
            }
    return out


async def _store_transcript(application_id: UUID, voice_call_id: UUID, text: str) -> str:
    bucket = get_settings().r2_bucket_resumes
    key = f"voice-calls/{application_id}/{voice_call_id}/transcript.txt"
    await upload_blob(
        bucket=bucket,
        key=key,
        content=text.encode("utf-8"),
        content_type="text/plain; charset=utf-8",
    )
    return key


async def _fetch_and_store_recording(
    application_id: UUID, voice_call_id: UUID, conversation_id: str
) -> str | None:
    """Pull the recording mp3 from ElevenLabs and store in R2."""
    settings = get_settings()
    if not settings.elevenlabs_api_key:
        return None
    url = f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}/audio"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(url, headers={"xi-api-key": settings.elevenlabs_api_key})
            resp.raise_for_status()
            audio_bytes = resp.content
    except httpx.HTTPError as exc:
        logger.warning("elevenlabs audio fetch failed for %s: %s", conversation_id, exc)
        return None

    bucket = settings.r2_bucket_resumes
    key = f"voice-calls/{application_id}/{voice_call_id}/recording.mp3"
    await upload_blob(bucket=bucket, key=key, content=audio_bytes, content_type="audio/mpeg")
    return key


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/elevenlabs", status_code=status.HTTP_202_ACCEPTED)
async def elevenlabs_webhook(
    request: Request,
    background: BackgroundTasks,
    elevenlabs_signature: str | None = Header(default=None),
    x_elevenlabs_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    raw = await request.body()
    logger.info(
        "elevenlabs webhook received: %d bytes, sig=%s, x-sig=%s",
        len(raw),
        bool(elevenlabs_signature),
        bool(x_elevenlabs_signature),
    )
    _verify_signature(raw, elevenlabs_signature or x_elevenlabs_signature)
    try:
        payload = ElPayload.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "elevenlabs webhook payload validation failed: %s\nraw body (first 2000 chars): %s",
            exc,
            raw[:2000].decode("utf-8", errors="replace"),
        )
        # Fallback: try to extract conversation_id from raw JSON and build
        # a minimal payload. ElevenLabs sometimes sends events in slightly
        # different shapes (e.g. flat structure, "event" instead of "type").
        import json as _json

        try:
            raw_dict = _json.loads(raw)
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid payload: {exc}")
        conv_id = (
            (raw_dict.get("data") or {}).get("conversation_id")
            or raw_dict.get("conversation_id")
        )
        event_type = raw_dict.get("type") or raw_dict.get("event") or "unknown"
        if not conv_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid payload: {exc}")
        logger.warning(
            "elevenlabs webhook: using fallback parsing for conversation_id=%s type=%s",
            conv_id,
            event_type,
        )
        data_raw = raw_dict.get("data") or raw_dict
        payload = ElPayload(
            type=event_type,
            event_timestamp=raw_dict.get("event_timestamp"),
            data=ElData(
                agent_id=data_raw.get("agent_id"),
                conversation_id=conv_id,
                status=data_raw.get("status"),
                transcript=[
                    ElTranscriptTurn(**t)
                    for t in (data_raw.get("transcript") or [])
                    if isinstance(t, dict)
                ],
                metadata=ElMetadata(**(data_raw.get("metadata") or {}))
                if data_raw.get("metadata")
                else None,
                analysis=data_raw.get("analysis"),
            ),
        )

    data = payload.data
    conversation_id = data.conversation_id

    # Look up our voice_calls row by conversation_id.
    async with session_scope() as session:
        voice = await get_by_provider_id(
            session, provider="elevenlabs", provider_call_id=conversation_id
        )
        if voice is None:
            logger.warning("elevenlabs webhook for unknown conversation_id=%s", conversation_id)
            raise HTTPException(status.HTTP_404_NOT_FOUND, "voice call not found")
        application_id = voice.application_id
        voice_call_id = voice.id
        questions = voice.questions
        attempt_no = voice.attempt_no
        already_completed = bool(voice.transcript_r2_key)
        # Explicit call kind column. Pre-0014 rows without the column default
        # to 'screening'; backfill in the migration flipped legacy
        # no-questions rows to 'confirmation'.
        call_kind = getattr(voice, "call_kind", "screening")
        is_confirmation = call_kind == "confirmation"
        is_informational = call_kind in (
            "status_update", "joining_details", "general_query", "meeting_schedule"
        )

    # ------------------------------------------------------------------
    # Audio-only follow-up event: store the recording and exit.
    # ------------------------------------------------------------------
    if payload.type == "post_call_audio":
        recording_key = await _fetch_and_store_recording(
            application_id, voice_call_id, conversation_id
        )
        if recording_key:
            async with session_scope() as session:
                row = await get_by_provider_id(
                    session, provider="elevenlabs", provider_call_id=conversation_id
                )
                if row is not None and not row.recording_r2_key:
                    row.recording_r2_key = recording_key
        return {"ok": True, "audio_stored": bool(recording_key)}

    # ------------------------------------------------------------------
    # Conversation started.
    # ------------------------------------------------------------------
    if payload.type == "conversation_started" or data.status == "in_progress":
        async with session_scope() as session:
            await mark_in_progress(session, voice_call_id)
            if not is_informational:
                await set_stage(
                    session, application_id, PipelineStage.VOICE_SCREEN_IN_PROGRESS, force=True
                )
            await log_audit(
                session,
                application_id=application_id,
                action="voice_call_started",
                actor="agent",
                details={"voice_call_id": str(voice_call_id), "conversation_id": conversation_id},
            )
        return {"ok": True}

    # ------------------------------------------------------------------
    # Failure / no-answer terminal. Retry with exponential backoff up to
    # ``voice_agent_max_noanswer_attempts`` before parking for HR.
    # ------------------------------------------------------------------
    if data.status == "failed":
        settings = get_settings()
        # ElevenLabs marks call_status=no-answer / failed on the data block.
        # Look at the metadata + the analysis section for "termination_reason".
        no_answer = False
        try:
            term_reason = (
                (data.analysis or {}).get("call_summary", {}).get("termination_reason")
                or (data.metadata.cost or {}).get("termination_reason")
                if data.metadata else None
            )
            if isinstance(term_reason, str) and "no" in term_reason.lower() and "answer" in term_reason.lower():
                no_answer = True
        except Exception:  # noqa: BLE001
            pass
        # Heuristic: zero-duration calls are no-answers.
        dur = (data.metadata.call_duration_secs if data.metadata else 0) or 0
        if dur < 5:
            no_answer = True

        # Voicemail detection: short call with no candidate speech, or
        # typical voicemail greeting phrases in the agent/system transcript.
        is_voicemail = False
        if not no_answer and dur > 0:
            _transcript = data.transcript or []
            candidate_spoke = any(
                t.role in ("user", "candidate") and t.message and len(t.message.strip()) > 5
                for t in _transcript
                if isinstance(t, ElTranscriptTurn)
            )
            if not candidate_spoke and dur < 30:
                is_voicemail = True
            # Check for voicemail phrases in all turns
            all_text = " ".join(
                (t.message or "") for t in _transcript if isinstance(t, ElTranscriptTurn)
            ).lower()
            voicemail_phrases = (
                "leave a message", "voicemail", "after the beep", "after the tone",
                "not available", "unavailable", "please leave", "record your message",
                "mailbox", "greeting", "press 1 to leave",
            )
            if any(p in all_text for p in voicemail_phrases):
                is_voicemail = True

        max_retry = settings.voice_agent_max_noanswer_attempts
        backoff_base = settings.voice_agent_noanswer_backoff_seconds
        async with session_scope() as session:
            await log_audit(
                session,
                application_id=application_id,
                action="voice_call_failed",
                actor="agent",
                details={
                    "voice_call_id": str(voice_call_id),
                    "conversation_id": conversation_id,
                    "no_answer": no_answer,
                    "is_voicemail": is_voicemail,
                    "duration_sec": dur,
                    "attempt_no": attempt_no,
                },
            )

        if (no_answer or is_voicemail) and attempt_no < max_retry:
            # Exponential backoff: base * 2^(attempt_no-1), then clamped into
            # the configured call window so retries never land at midnight.
            delay = backoff_base * (2 ** (attempt_no - 1))
            from datetime import timedelta as _td

            desired_at = datetime.now(UTC) + _td(seconds=delay)
            retry_at, was_clamped = clamp_to_call_window(desired_at)
            effective_delay = max(0, int((retry_at - datetime.now(UTC)).total_seconds()))

            async with session_scope() as session:
                await mark_failed(
                    session,
                    voice_call_id,
                    error=f"{'voicemail' if is_voicemail else 'no_answer'} attempt={attempt_no}",
                    status=VoiceCallStatus.VOICEMAIL if is_voicemail else VoiceCallStatus.NO_ANSWER,
                )
                row = await session.get(VoiceCall, voice_call_id)
                if row is not None:
                    row.callback_at = retry_at
                    row.callback_reason = (
                        f"Voicemail detected — auto-retry #{attempt_no + 1} scheduled"
                        if is_voicemail
                        else f"Auto-retry #{attempt_no + 1} scheduled"
                    )
                if was_clamped:
                    await log_audit(
                        session,
                        application_id=application_id,
                        action="voice_call_retry_clamped_to_window",
                        actor="agent",
                        details={
                            "voice_call_id": str(voice_call_id),
                            "raw_delay_seconds": delay,
                            "desired_at": desired_at.isoformat(),
                            "retry_at": retry_at.isoformat(),
                        },
                    )
            # Use correct dispatcher based on call kind
            if call_kind != "screening":
                queued = await enqueue(
                    "dispatch_voice_call",
                    str(application_id),
                    call_kind=call_kind,
                    attempt_no=attempt_no + 1,
                    scheduled_at_iso=retry_at.isoformat(),
                    _defer_until=retry_at,
                    _job_id=f"voice-retry-{voice_call_id}-{attempt_no + 1}",
                )
            else:
                queued = await enqueue(
                    "dispatch_voice_screening",
                    str(application_id),
                    attempt_no=attempt_no + 1,
                    scheduled_at_iso=retry_at.isoformat(),
                    _defer_until=retry_at,
                    _job_id=f"voice-noanswer-{voice_call_id}-{attempt_no + 1}",
                )
            if not queued:
                if call_kind != "screening":
                    background.add_task(
                        dispatch_voice_call,
                        application_id=application_id,
                        call_kind=call_kind,
                        attempt_no=attempt_no + 1,
                        scheduled_at=retry_at,
                    )
                else:
                    background.add_task(
                        dispatch_voice_screening,
                        application_id=application_id,
                        attempt_no=attempt_no + 1,
                        scheduled_at=retry_at,
                    )
            await publish_event(
                application_id,
                event="voice_call_voicemail" if is_voicemail else "voice_call_no_answer",
                data={
                    "voice_call_id": str(voice_call_id),
                    "attempt_no": attempt_no,
                    "is_voicemail": is_voicemail,
                    "next_attempt_in_seconds": effective_delay,
                    "retry_at": retry_at.isoformat(),
                },
            )
            return {
                "ok": True,
                "retry_in_seconds": effective_delay,
                "retry_at": retry_at.isoformat(),
                "attempt_no": attempt_no + 1,
            }

        # Out of retries (or non no-answer/voicemail failure) -- park for HR.
        async with session_scope() as session:
            await mark_failed(
                session,
                voice_call_id,
                error=(
                    f"{'voicemail' if is_voicemail else 'no_answer'} max retries reached"
                    if (no_answer or is_voicemail)
                    else "elevenlabs status=failed"
                ),
                status=VoiceCallStatus.VOICEMAIL if is_voicemail else VoiceCallStatus.FAILED,
            )
            await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
            await publish_supervisor_event(
                session,
                EventType.VOICE_CALL_FAILED,
                application_id=application_id,
                payload={
                    "voice_call_id": str(voice_call_id),
                    "no_answer": no_answer,
                    "is_voicemail": is_voicemail,
                    "attempt_no": attempt_no,
                    "reason": (
                        "voicemail_max_retries_exhausted" if is_voicemail
                        else "max_retries_exhausted" if no_answer
                        else "call_failed"
                    ),
                },
                dedup_extra=f"failed-{voice_call_id}",
            )
        return {"ok": True, "parked_for_hr": True}

    # ------------------------------------------------------------------
    # Done -- transcript event with status=done OR a generic post_call_transcription.
    # ------------------------------------------------------------------
    if already_completed:
        return {"ok": True, "duplicate": True}

    # Atomic claim: first webhook delivery for this voice_call_id wins. A
    # concurrent retry from ElevenLabs (after our previous response timed out)
    # arriving in parallel will get rowcount==0 and bail without re-running
    # the evaluator. The sentinel is overwritten with the real key below.
    _sentinel = f"_in_flight:{conversation_id}"
    async with session_scope() as session:
        from src.db.repositories.voice_call import claim_completion

        claimed = await claim_completion(session, voice_call_id, _sentinel)
    if not claimed:
        logger.info(
            "voice webhook for %s already claimed by concurrent delivery; bailing",
            conversation_id,
        )
        return {"ok": True, "duplicate": True}

    # Confirmation-call path: no question scoring, no stage transitions on
    # the screening pipeline. We only react to the CONFIRM=... marker.
    if is_confirmation:
        decision, requested_at = _extract_confirmation(data.transcript)
        transcript_text = _transcript_text(data.transcript)
        transcript_key = await _store_transcript(
            application_id, voice_call_id, transcript_text
        )
        async with session_scope() as session:
            await save_call_completion(
                session,
                voice_call_id,
                answers=[],
                transcript_r2_key=transcript_key,
                recording_r2_key=None,
                duration_sec=(data.metadata.call_duration_secs if data.metadata else None),
                ended_at=datetime.now(UTC),
            )
            await log_audit(
                session,
                application_id=application_id,
                action="meeting_confirmation_call_completed",
                actor="agent",
                details={
                    "voice_call_id": str(voice_call_id),
                    "decision": decision,
                    "requested_at": requested_at.isoformat() if requested_at else None,
                },
            )
        # Schedule any follow-up work in the background (re-pick slot etc).
        from src.activities.v1_schedule_meeting import schedule_meeting

        if decision == "reschedule":
            meta = (voice.questions or {}) if isinstance(voice.questions, dict) else {}
            round_name = meta.get("round") or "technical"
            queued = await enqueue(
                "schedule_meeting_reattempt",
                str(application_id),
                voice_call_id=str(voice_call_id),
                requested_at_iso=requested_at.isoformat() if requested_at else None,
                round=round_name,
            )
            if not queued:
                avoid: list[datetime] = []
                # We don't know the originally-proposed start here; pulling it
                # from the most recent meeting_session is sufficient.
                background.add_task(
                    schedule_meeting,
                    application_id=application_id,
                    round=round_name,
                    avoid_starts=avoid,
                    attempt_no=attempt_no + 1,
                )
        # decision="yes" -> stage already at *_meeting_scheduled; nothing to do.
        # decision="no" or unknown -> park for HR.
        if decision in {None, "no"}:
            async with session_scope() as session:
                await set_stage(
                    session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True
                )
        await publish_event(
            application_id,
            event="meeting_confirmation_done",
            data={
                "voice_call_id": str(voice_call_id),
                "decision": decision,
                "requested_at": requested_at.isoformat() if requested_at else None,
            },
        )
        return {"ok": True, "decision": decision}

    # Informational calls: store transcript, no scoring, no stage change.
    if is_informational:
        transcript_text = _transcript_text(data.transcript)
        transcript_key = await _store_transcript(application_id, voice_call_id, transcript_text)
        recording_key = await _fetch_and_store_recording(
            application_id, voice_call_id, conversation_id
        )
        _duration = data.metadata.call_duration_secs if data.metadata else None

        # Handle meeting_schedule decisions the same as confirmation calls
        if call_kind == "meeting_schedule":
            decision, requested_at = _extract_confirmation(data.transcript)
        else:
            decision, requested_at = None, None

        # Check for callback requests on informational calls too
        cb_at, cb_reason = _extract_callback(data.transcript)
        if cb_at is None:
            cb_at, _ar = _extract_callback_from_analysis(data.analysis)
            if cb_reason is None:
                cb_reason = _ar
        if cb_at is None:
            cb_at, _nr = _extract_callback_natural(data.transcript)
            if cb_reason is None:
                cb_reason = _nr

        async with session_scope() as session:
            if cb_at is not None:
                cb_at_clamped, _ = clamp_to_call_window(cb_at)
                await save_callback_request(
                    session, voice_call_id, callback_at=cb_at_clamped, reason=cb_reason
                )
            else:
                await save_call_completion(
                    session,
                    voice_call_id,
                    answers=[],
                    transcript_r2_key=transcript_key,
                    recording_r2_key=recording_key,
                    duration_sec=_duration,
                    ended_at=datetime.now(UTC),
                )
            await log_audit(
                session,
                application_id=application_id,
                action=f"voice_{call_kind}_completed",
                actor="agent",
                details={
                    "voice_call_id": str(voice_call_id),
                    "call_kind": call_kind,
                    "duration_sec": _duration,
                    "decision": decision,
                },
            )

        if call_kind == "meeting_schedule":
            from src.services.smart_scheduler import handle_candidate_response
            # Find the active meeting session for this application
            async with session_scope() as session:
                from sqlalchemy import select as sa_select
                from src.db.base import MeetingSession
                ms = (await session.execute(
                    sa_select(MeetingSession).where(
                        MeetingSession.application_id == application_id,
                        MeetingSession.bot_status == "negotiating",
                    ).order_by(MeetingSession.created_at.desc()).limit(1)
                )).scalar_one_or_none()
                ms_id = ms.id if ms else None

            if ms_id:
                background.add_task(
                    handle_candidate_response,
                    meeting_session_id=ms_id,
                    decision=decision or "no",
                    preferred_at=requested_at,
                )
            elif decision == "reschedule":
                v_meta = (voice.questions or {}) if isinstance(voice.questions, dict) else {}
                await enqueue(
                    "schedule_meeting_reattempt",
                    str(application_id),
                    voice_call_id=str(voice_call_id),
                    requested_at_iso=requested_at.isoformat() if requested_at else None,
                    round=v_meta.get("round") or "technical",
                )

        await publish_event(
            application_id,
            event=f"voice_{call_kind}_completed",
            data={
                "voice_call_id": str(voice_call_id),
                "call_kind": call_kind,
                "duration_sec": _duration,
            },
        )
        return {"ok": True, "call_kind": call_kind, "voice_call_id": str(voice_call_id)}

    # Three-tier callback extraction:
    #   1. Explicit ``CALLBACK_AT=...`` marker in agent transcript.
    #   2. ElevenLabs Data Collection results (analysis block) -- preferred.
    #   3. Natural-language regex fallback over the agent's last few turns.
    # Without 2/3, an agent that confirms a callback verbally instead of
    # emitting the marker leaves the candidate stuck in "completed" with no
    # callback row -> nothing redials, nothing surfaces in the UI.
    callback_at_raw, callback_reason = _extract_callback(data.transcript)
    if callback_at_raw is None:
        callback_at_raw, analysis_reason = _extract_callback_from_analysis(
            data.analysis
        )
        if callback_reason is None:
            callback_reason = analysis_reason
    if callback_at_raw is None:
        callback_at_raw, natural_reason = _extract_callback_natural(data.transcript)
        if callback_reason is None:
            callback_reason = natural_reason
    if callback_at_raw is not None:
        # Candidate may give an off-hours time ("call me at 2am"). Clamp to
        # the next valid window so we never auto-dial outside business hours.
        callback_at, callback_clamped = clamp_to_call_window(callback_at_raw)
        async with session_scope() as session:
            await save_callback_request(
                session, voice_call_id, callback_at=callback_at, reason=callback_reason
            )
            await set_stage(
                session, application_id, PipelineStage.VOICE_SCREEN_CALLBACK_REQUESTED, force=True
            )
            await log_audit(
                session,
                application_id=application_id,
                action="voice_call_callback_requested",
                actor="agent",
                details={
                    "voice_call_id": str(voice_call_id),
                    "callback_at": callback_at.isoformat(),
                    "callback_at_requested": callback_at_raw.isoformat(),
                    "clamped_to_window": callback_clamped,
                    "reason": (callback_reason or "")[:200],
                },
            )
        queued = await enqueue(
            "dispatch_voice_screening",
            str(application_id),
            attempt_no=attempt_no + 1,
            scheduled_at_iso=callback_at.isoformat(),
            _defer_until=callback_at,
            _job_id=f"voice-callback-{voice_call_id}-{attempt_no + 1}",
        )
        if not queued and callback_at <= datetime.now(UTC):
            background.add_task(
                dispatch_voice_screening,
                application_id=application_id,
                attempt_no=attempt_no + 1,
                scheduled_at=callback_at,
            )
        # Supervisor event
        async with session_scope() as sess:
            await publish_supervisor_event(
                sess,
                EventType.VOICE_CALLBACK_REQUESTED,
                application_id=application_id,
                payload={
                    "voice_call_id": str(voice_call_id),
                    "callback_at": callback_at.isoformat(),
                    "reason": (callback_reason or "")[:200],
                },
                dedup_extra=f"callback-{voice_call_id}",
            )
        return {
            "ok": True,
            "callback_scheduled_for": callback_at.isoformat(),
            "clamped_to_window": callback_clamped,
        }

    # Regular completion: persist transcript + answers, kick evaluator.
    transcript_text = _transcript_text(data.transcript)
    transcript_key = await _store_transcript(application_id, voice_call_id, transcript_text)
    answers_jsonable = _zip_answers(questions, data.transcript)
    duration_sec = data.metadata.call_duration_secs if data.metadata else None
    ended_at = datetime.now(UTC)

    # Edge case: telephony / agent dropped the call before the candidate
    # could finish answering. ElevenLabs may still report status=done.
    # Detect via any of:
    #   * candidate spoke 0 user turns AND duration < 25s (original case)
    #   * ElevenLabs termination_reason signals a drop (client_disconnected,
    #     silence_timeout, error, network_error, agent_disconnect, etc.)
    #   * candidate answered <50% of the questions with any non-empty text
    #     AND total call duration < 60s (partial drop)
    # In any of these cases retry instead of letting the evaluator score a
    # near-blank transcript and reject a real candidate.
    settings = get_settings()
    user_turns = sum(
        1
        for t in data.transcript
        if (t.role or "").lower() == "user" and (t.message or "").strip()
    )
    term_reason_raw = ""
    try:
        cs = (data.analysis or {}).get("call_summary") or {}
        term_reason_raw = (cs.get("termination_reason") or "").lower()
    except Exception:  # noqa: BLE001
        term_reason_raw = ""
    _DROP_SIGNALS = (
        "client_disconnected",
        "client disconnected",
        "user_hangup_early",
        "silence_timeout",
        "silence timeout",
        "network_error",
        "network error",
        "agent_disconnect",
        "agent disconnect",
        "error",
        "twilio_error",
        "call_dropped",
        "no_audio",
    )
    drop_signal = any(s in term_reason_raw for s in _DROP_SIGNALS)
    answered = sum(
        1
        for a in (answers_jsonable or [])
        if (a.get("answer_transcript") or "").strip()
    )
    q_total = len(questions) if isinstance(questions, list) else 0
    answer_ratio = (answered / q_total) if q_total else 0.0
    total_answer_chars = sum(
        len((a.get("answer_transcript") or "").strip())
        for a in (answers_jsonable or [])
    )
    early_disconnect_threshold_sec = 25
    early_disconnect = (
        # original: no user audio at all on a short call
        (user_turns == 0 and (duration_sec or 0) < early_disconnect_threshold_sec)
        # provider says the line dropped
        or drop_signal
        # candidate answered less than half the questions on a short call
        or (q_total >= 2 and answer_ratio < 0.5 and (duration_sec or 0) < 60)
        # transcript carries effectively no content
        or (total_answer_chars < 40 and (duration_sec or 0) < 90)
    )
    if early_disconnect and attempt_no >= settings.voice_agent_max_noanswer_attempts:
        # Retries exhausted -- park for HR instead of evaluating a blank transcript.
        async with session_scope() as session:
            await mark_failed(
                session,
                voice_call_id,
                error=f"early_disconnect_max_retries dur={duration_sec}s attempt={attempt_no}",
                status=VoiceCallStatus.FAILED,
            )
            await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
            await log_audit(
                session,
                application_id=application_id,
                action="voice_call_disconnected_max_retries",
                actor="agent",
                details={
                    "voice_call_id": str(voice_call_id),
                    "conversation_id": conversation_id,
                    "duration_sec": duration_sec,
                    "attempt_no": attempt_no,
                    "max_attempts": settings.voice_agent_max_noanswer_attempts,
                },
            )
        await publish_event(
            application_id,
            event="voice_call_failed_max_retries",
            data={"voice_call_id": str(voice_call_id), "attempt_no": attempt_no},
        )
        return {"ok": True, "parked_for_hr": True, "reason": "early_disconnect_max_retries"}

    if early_disconnect:
        retry_at, _ = clamp_to_call_window(datetime.now(UTC))
        async with session_scope() as session:
            await mark_failed(
                session,
                voice_call_id,
                error=f"agent_disconnected_early dur={duration_sec}s user_turns=0",
                status=VoiceCallStatus.FAILED,
            )
            row = await session.get(VoiceCall, voice_call_id)
            if row is not None:
                row.callback_at = retry_at
                row.callback_reason = f"Auto-retry #{attempt_no + 1} (early disconnect)"
            await log_audit(
                session,
                application_id=application_id,
                action="voice_call_disconnected_early",
                actor="agent",
                details={
                    "voice_call_id": str(voice_call_id),
                    "conversation_id": conversation_id,
                    "duration_sec": duration_sec,
                    "user_turns": user_turns,
                    "answered_questions": answered,
                    "total_questions": q_total,
                    "total_answer_chars": total_answer_chars,
                    "termination_reason": term_reason_raw or None,
                    "drop_signal": drop_signal,
                    "attempt_no": attempt_no,
                    "retry_at": retry_at.isoformat(),
                },
            )
        queued = await enqueue(
            "dispatch_voice_screening",
            str(application_id),
            attempt_no=attempt_no + 1,
            scheduled_at_iso=retry_at.isoformat(),
            _defer_until=retry_at,
            _job_id=f"voice-earlydisc-{voice_call_id}-{attempt_no + 1}",
        )
        if not queued:
            background.add_task(
                dispatch_voice_screening,
                application_id=application_id,
                attempt_no=attempt_no + 1,
                scheduled_at=retry_at,
            )
        await publish_event(
            application_id,
            event="voice_call_disconnected_early",
            data={
                "voice_call_id": str(voice_call_id),
                "attempt_no": attempt_no,
                "next_attempt_at": retry_at.isoformat(),
            },
        )
        return {
            "ok": True,
            "early_disconnect": True,
            "retry_at": retry_at.isoformat(),
        }

    # Audio: try to fetch up-front; ElevenLabs often delivers it via a separate
    # event, so failure here is non-fatal.
    recording_key = await _fetch_and_store_recording(
        application_id, voice_call_id, conversation_id
    )

    async with session_scope() as session:
        await save_call_completion(
            session,
            voice_call_id,
            answers=answers_jsonable,
            transcript_r2_key=transcript_key,
            recording_r2_key=recording_key,
            duration_sec=duration_sec,
            ended_at=ended_at,
        )
        await set_stage(
            session, application_id, PipelineStage.VOICE_SCREEN_COMPLETED, force=True
        )
        await log_audit(
            session,
            application_id=application_id,
            action="voice_call_completed",
            actor="agent",
            details={
                "voice_call_id": str(voice_call_id),
                "conversation_id": conversation_id,
                "duration_sec": duration_sec,
                "answer_count": len(answers_jsonable),
            },
        )

    # Paralinguistic enrichment via the local emotion service when available.
    paralinguistic: EmotionFeatures | None = None
    paralinguistic_payload: dict[str, Any] | None = None
    if recording_key is not None:
        try:
            bucket = get_settings().r2_bucket_resumes
            audio_url = await presigned_get_url(bucket, recording_key, ttl_seconds=900)
            paralinguistic = await analyze_recording(audio_url=audio_url)
            if paralinguistic is not None:
                paralinguistic_payload = paralinguistic.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            logger.warning("emotion enrichment failed: %s", exc)

    queued = await enqueue(
        "evaluate_voice_call",
        str(voice_call_id),
        paralinguistic=paralinguistic_payload,
    )
    if not queued:
        background.add_task(
            evaluate_voice_call,
            voice_call_id=voice_call_id,
            paralinguistic=paralinguistic,
        )
    await publish_event(
        application_id,
        event="voice_call_completed",
        data={
            "voice_call_id": str(voice_call_id),
            "duration_sec": duration_sec,
            "answer_count": len(answers_jsonable),
        },
    )
    # Supervisor event — typed bus
    async with session_scope() as sess:
        await publish_supervisor_event(
            sess,
            EventType.VOICE_CALL_COMPLETED,
            application_id=application_id,
            payload={
                "voice_call_id": str(voice_call_id),
                "duration_sec": duration_sec,
                "answer_count": len(answers_jsonable),
            },
            dedup_extra=str(voice_call_id),
        )
    return {"ok": True, "voice_call_id": str(voice_call_id)}


# ---------------------------------------------------------------------------
# Recovery: poll ElevenLabs API for a stuck conversation
# ---------------------------------------------------------------------------


@router.post("/elevenlabs/recover/{conversation_id}", status_code=status.HTTP_200_OK)
async def recover_elevenlabs_conversation(
    conversation_id: str,
    background: BackgroundTasks,
) -> dict[str, Any]:
    """Manually recover a voice call by polling ElevenLabs conversation API.

    Use when the webhook failed (400/500) but the call completed on ElevenLabs.
    Fetches the conversation transcript directly from ElevenLabs and processes it
    as if the webhook had arrived successfully.
    """
    settings = get_settings()
    if not settings.elevenlabs_api_key:
        raise HTTPException(500, "ELEVENLABS_API_KEY not configured")

    # Look up our voice_call row
    async with session_scope() as session:
        voice = await get_by_provider_id(
            session, provider="elevenlabs", provider_call_id=conversation_id
        )
        if voice is None:
            raise HTTPException(404, f"No voice_call found for conversation_id={conversation_id}")
        if voice.transcript_r2_key:
            return {"ok": True, "already_completed": True, "voice_call_id": str(voice.id)}
        application_id = voice.application_id
        voice_call_id = voice.id
        questions = voice.questions
        attempt_no = voice.attempt_no
        call_kind = getattr(voice, "call_kind", "screening")

    # Fetch conversation from ElevenLabs API
    url = f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            url, headers={"xi-api-key": settings.elevenlabs_api_key}
        )
        if resp.status_code == 404:
            raise HTTPException(404, "Conversation not found on ElevenLabs")
        resp.raise_for_status()
        conv_data = resp.json()

    logger.info("elevenlabs recovery: fetched conversation %s, status=%s", conversation_id, conv_data.get("status"))

    el_status = conv_data.get("status", "unknown")
    if el_status not in ("done", "ended"):
        return {"ok": False, "status": el_status, "message": "Conversation not yet completed on ElevenLabs"}

    # Build transcript from ElevenLabs response format
    raw_transcript = conv_data.get("transcript") or []
    transcript_turns = []
    for t in raw_transcript:
        if isinstance(t, dict):
            transcript_turns.append(ElTranscriptTurn(
                role=t.get("role", "user"),
                message=t.get("message") or t.get("text") or "",
                time_in_call_secs=t.get("time_in_call_secs"),
            ))

    el_metadata = conv_data.get("metadata") or {}
    duration_sec = el_metadata.get("call_duration_secs")
    analysis = conv_data.get("analysis")

    # Build a synthetic payload and pass through the normal webhook flow
    payload = ElPayload(
        type="post_call_transcription",
        data=ElData(
            agent_id=conv_data.get("agent_id"),
            conversation_id=conversation_id,
            status="done",
            transcript=transcript_turns,
            metadata=ElMetadata(
                call_duration_secs=duration_sec,
                cost=el_metadata.get("cost"),
            ) if el_metadata else None,
            analysis=analysis,
        ),
    )

    # Process using the same logic as the webhook handler.
    # We skip signature verification since this is an authenticated internal call.
    is_confirmation = call_kind == "confirmation"
    is_informational = call_kind in ("status_update", "joining_details", "general_query", "meeting_schedule")

    # For screening calls: store transcript + answers, kick evaluator
    transcript_text = _transcript_text(payload.data.transcript)
    transcript_key = await _store_transcript(application_id, voice_call_id, transcript_text)
    answers_jsonable = _zip_answers(questions, payload.data.transcript)
    ended_at = datetime.now(UTC)

    recording_key = await _fetch_and_store_recording(
        application_id, voice_call_id, conversation_id
    )

    async with session_scope() as session:
        await save_call_completion(
            session,
            voice_call_id,
            answers=answers_jsonable,
            transcript_r2_key=transcript_key,
            recording_r2_key=recording_key,
            duration_sec=duration_sec,
            ended_at=ended_at,
        )
        if not is_informational:
            await set_stage(
                session, application_id, PipelineStage.VOICE_SCREEN_COMPLETED, force=True
            )
        await log_audit(
            session,
            application_id=application_id,
            action="voice_call_recovered",
            actor="system",
            details={
                "voice_call_id": str(voice_call_id),
                "conversation_id": conversation_id,
                "duration_sec": duration_sec,
                "answer_count": len(answers_jsonable),
                "recovery": True,
            },
        )

    # Kick evaluator for screening calls
    if not is_confirmation and not is_informational:
        queued = await enqueue(
            "evaluate_voice_call",
            str(voice_call_id),
        )
        if not queued:
            background.add_task(
                evaluate_voice_call,
                voice_call_id=voice_call_id,
            )

    await publish_event(
        application_id,
        event="voice_call_completed",
        data={
            "voice_call_id": str(voice_call_id),
            "duration_sec": duration_sec,
            "answer_count": len(answers_jsonable),
            "recovered": True,
        },
    )

    logger.info(
        "elevenlabs recovery: completed voice_call=%s conversation=%s answers=%d",
        voice_call_id, conversation_id, len(answers_jsonable),
    )
    return {
        "ok": True,
        "voice_call_id": str(voice_call_id),
        "recovered": True,
        "answer_count": len(answers_jsonable),
        "duration_sec": duration_sec,
    }
