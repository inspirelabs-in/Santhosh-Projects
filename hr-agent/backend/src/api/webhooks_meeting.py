"""Meeting webhooks (Read.ai primary, Recall.ai legacy).

Read.ai posts ``meeting.completed`` events to ``/webhooks/meeting/readai``
once a transcript is available. Body shape (canonical):

    {
      "event": "meeting.completed",
      "report_id": "rep_xxx",
      "meeting_url": "https://teams.microsoft.com/...",
      "start_time": "2026-05-04T12:00:00Z",
      "end_time":   "2026-05-04T12:42:00Z",
      "organiser_email": "careers@grabon.in",
      "transcript_url": "https://api.read.ai/v1/reports/rep_xxx/transcript",
      "recording_url":  "https://...mp4"   # optional
    }

We match the report to a ``meeting_session`` row by ``meeting_url`` (preferred)
or by organiser+scheduled_at window, then download the transcript + recording
and queue the analyzer.

Recall.ai endpoint kept for back-compat -- only fires when
``MEETING_BOT_PROVIDER=recall``.
"""

from __future__ import annotations

import hmac
import json
import logging
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from pydantic import BaseModel

from src.activities.v1_meeting_analysis import analyze_meeting
from src.config import get_settings
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.meeting_session import (
    attach_bot,
    get_by_bot_id,
    get_by_join_url,
    get_by_time_window,
    mark_failed,
    mark_started,
    save_artifacts,
)
from src.services.events import publish_event
from src.services.file_storage import upload_blob
from src.services.meeting_bot import get_meeting_bot_provider
from src.services.queue import enqueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/meeting", tags=["meeting-webhooks"])


async def _emit_meeting_supervisor_event(
    application_id: UUID | None,
    meeting_session_id: UUID,
    round_value: str | None,
    duration_sec: float | None,
    transcript: list[dict[str, Any]],
) -> None:
    """Emit typed supervisor event for meeting completion or no-show."""
    from src.services.typed_event_bus import EventType
    from src.services.typed_event_bus import publish_event as publish_typed_event

    is_no_show = (
        (not transcript and (duration_sec is None or duration_sec < 60))
        or (
            len(transcript or []) == 1
            and transcript[0].get("speaker") == "Read.ai Summary"
            and not transcript[0].get("text")
        )
    )
    async with session_scope() as session:
        event_type = EventType.MEETING_NO_SHOW if is_no_show else EventType.MEETING_COMPLETED
        await publish_typed_event(
            session,
            event_type,
            application_id=application_id,
            payload={
                "meeting_session_id": str(meeting_session_id),
                "round": round_value,
                "duration_sec": duration_sec,
                "transcript_turns": len(transcript),
                "is_no_show": is_no_show,
            },
            dedup_extra=str(meeting_session_id),
        )


class RecallPayload(BaseModel):
    event: str
    bot_id: str | None = None
    data: dict[str, Any] | None = None


def _verify_signature(raw: bytes, signature: str | None) -> None:
    settings = get_settings()
    secret = settings.recall_ai_webhook_secret
    if not secret:
        logger.warning("recall_ai_webhook_secret not configured — rejecting unsigned webhook")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Webhook secret not configured")
    if not signature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing signature")
    expected = hmac.new(secret.encode("utf-8"), raw, sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")


@router.post("/recall", status_code=status.HTTP_202_ACCEPTED)
async def recall_webhook(
    request: Request,
    background: BackgroundTasks,
    x_recall_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    raw = await request.body()
    _verify_signature(raw, x_recall_signature)
    try:
        payload = RecallPayload.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid payload: {exc}")

    bot_id = payload.bot_id or (payload.data or {}).get("bot", {}).get("id")
    if not bot_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bot_id missing")

    async with session_scope() as session:
        meeting = await get_by_bot_id(session, bot_id=str(bot_id))
        if meeting is None:
            logger.warning("recall webhook for unknown bot_id=%s", bot_id)
            raise HTTPException(status.HTTP_404_NOT_FOUND, "meeting not found")
        meeting_session_id = meeting.id
        application_id = meeting.application_id
        round_value = meeting.round
        existing_transcript_key = meeting.transcript_r2_key

    if payload.event == "bot.in_call_recording":
        async with session_scope() as session:
            await mark_started(session, meeting_session_id)
            await log_audit(
                session,
                application_id=application_id,
                action="meeting_bot_in_call",
                actor="agent",
                details={"meeting_session_id": str(meeting_session_id), "round": round_value},
            )
        return {"ok": True}

    if payload.event in {"bot.fatal", "bot.failed"}:
        err = json.dumps((payload.data or {}).get("error") or "unknown")
        async with session_scope() as session:
            await mark_failed(session, meeting_session_id, error=err)
            await log_audit(
                session,
                application_id=application_id,
                action="meeting_bot_failed",
                actor="agent",
                details={
                    "meeting_session_id": str(meeting_session_id),
                    "error": err[:500],
                },
            )
        return {"ok": True}

    if payload.event != "bot.done":
        return {"ok": True, "ignored": payload.event}

    if existing_transcript_key:
        return {"ok": True, "duplicate": True}

    # Fetch transcript + recording. Both calls hit Recall.ai outbound.
    settings = get_settings()
    bucket = settings.r2_bucket_resumes
    provider = get_meeting_bot_provider()
    try:
        transcript = await provider.fetch_transcript(bot_id=str(bot_id))
    except Exception as exc:  # noqa: BLE001
        async with session_scope() as session:
            await mark_failed(
                session, meeting_session_id, error=f"fetch_transcript: {exc}"
            )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "transcript fetch failed")

    transcript_key = f"meetings/{application_id}/{meeting_session_id}/transcript.json"
    await upload_blob(
        bucket=bucket,
        key=transcript_key,
        content=json.dumps(transcript, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )

    recording_key: str | None = None
    try:
        recording_url = await provider.fetch_recording_url(bot_id=str(bot_id))
        if recording_url:
            audio_bytes = await provider.download_bytes(url=recording_url)
            recording_key = (
                f"meetings/{application_id}/{meeting_session_id}/recording.mp4"
            )
            await upload_blob(
                bucket=bucket,
                key=recording_key,
                content=audio_bytes,
                content_type="video/mp4",
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("recording capture failed for bot %s: %s", bot_id, exc)

    duration_sec: float | None = None
    if transcript:
        last = transcript[-1]
        if isinstance(last, dict):
            t_end = last.get("t_end") or last.get("end") or last.get("end_time")
            if isinstance(t_end, (int, float)):
                duration_sec = float(t_end)

    participants = (payload.data or {}).get("participants")
    ended_at = datetime.now(UTC)

    async with session_scope() as session:
        await save_artifacts(
            session,
            meeting_session_id,
            transcript_r2_key=transcript_key,
            recording_r2_key=recording_key,
            duration_sec=duration_sec,
            ended_at=ended_at,
            participants=participants if isinstance(participants, list) else None,
        )
        await log_audit(
            session,
            application_id=application_id,
            action="meeting_bot_done",
            actor="agent",
            details={
                "meeting_session_id": str(meeting_session_id),
                "duration_sec": duration_sec,
                "transcript_turns": len(transcript) if transcript else 0,
                "recording_captured": recording_key is not None,
            },
        )

    queued = await enqueue("analyze_meeting", str(meeting_session_id))
    if not queued:
        background.add_task(analyze_meeting, meeting_session_id=meeting_session_id)
    await publish_event(
        application_id,
        event="meeting_done",
        data={
            "meeting_session_id": str(meeting_session_id),
            "round": round_value,
            "duration_sec": duration_sec,
        },
    )
    await _emit_meeting_supervisor_event(application_id, meeting_session_id, round_value, duration_sec, transcript)
    return {"ok": True, "meeting_session_id": str(meeting_session_id)}


# ---------------------------------------------------------------------------
# Read.ai webhook  (calendar-trigger provider)
# ---------------------------------------------------------------------------


class ReadAIParticipant(BaseModel):
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None


class ReadAIPayload(BaseModel):
    """Schema matches the real Read.ai webhook payload (verified via debug log).
    Read.ai uses ``trigger`` (e.g. ``meeting_end``) not ``event``, and identifies
    meetings by ``session_id``. Pro/Enterprise additionally include
    ``meeting_url`` -- when present we correlate by URL match (exact, no
    window guesswork). Falls back to time-window match for older payloads.
    """
    session_id: str | None = None
    trigger: str | None = None
    title: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    participants: list[ReadAIParticipant] | None = None
    owner: ReadAIParticipant | None = None
    meeting_url: str | None = None
    # Optional fields Read.ai may include in completed reports
    report_url: str | None = None
    transcript_url: str | None = None
    recording_url: str | None = None
    summary: str | None = None
    # Read.ai sends transcript as {"speaker_blocks": [...]} dict, others
    # may be list-of-dict OR dict-of-list depending on report type. Accept any.
    transcript: list[dict[str, Any]] | dict[str, Any] | None = None
    action_items: list[dict[str, Any]] | dict[str, Any] | None = None
    topics: list[dict[str, Any]] | dict[str, Any] | None = None
    chapters: list[dict[str, Any]] | dict[str, Any] | None = None
    key_questions: list[dict[str, Any]] | dict[str, Any] | None = None


def _verify_readai_signature(raw: bytes, signature: str | None) -> None:
    """Read.ai signs webhook deliveries with HMAC-SHA256 over the raw body.
    Header name varies across vendor docs -- accept the common variants.
    Signing key comes from the Read.ai webhook dashboard ("Signing Key").
    """
    settings = get_settings()
    secret = settings.read_ai_webhook_secret
    if not secret:
        return  # dev mode -- no signing configured
    if not signature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing signature")
    sig = signature.strip()
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1]
    expected = hmac.new(secret.encode("utf-8"), raw, sha256).hexdigest()
    if not hmac.compare_digest(expected, sig.lower()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")


@router.post("/readai", status_code=status.HTTP_202_ACCEPTED)
async def readai_webhook(
    request: Request,
    background: BackgroundTasks,
    x_readai_signature: str | None = Header(default=None, alias="X-Readai-Signature"),
    x_read_signature: str | None = Header(default=None, alias="X-Read-Signature"),
    x_signature: str | None = Header(default=None, alias="X-Signature"),
) -> dict[str, Any]:
    raw = await request.body()
    logger.info(
        "readai webhook IN headers=%s body_preview=%s",
        {k: v for k, v in request.headers.items() if k.lower().startswith(("x-", "content-"))},
        raw[:500].decode("utf-8", errors="replace"),
    )
    _verify_readai_signature(raw, x_readai_signature or x_read_signature or x_signature)
    try:
        payload = ReadAIPayload.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("readai payload parse failed: %s | raw=%s", exc, raw[:300])
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid payload: {exc}")

    # Read.ai fires only meeting_end (and a few other lifecycle events). Ignore
    # anything that isn't a finished meeting.
    if (payload.trigger or "").lower() not in {"meeting_end", "meeting_complete", "meeting.completed"}:
        return {"ok": True, "ignored": payload.trigger}

    # Correlate Read.ai session -> our meeting_session row. Read.ai does NOT
    # send the Teams join URL, so match by Read.ai session_id (stored in
    # meeting_sessions.bot_id once we've seen this session before) OR by
    # organiser/scheduled-at window for a first-time match.
    if not payload.session_id:
        logger.info("readai payload missing session_id -- treating as test ping")
        return {"ok": True, "test": True}

    async with session_scope() as session:
        meeting = await get_by_bot_id(session, bot_id=payload.session_id)
        if meeting is None and payload.meeting_url:
            # Preferred: exact join_url match. No time-window collisions.
            meeting = await get_by_join_url(session, join_url=payload.meeting_url)
            if meeting is not None:
                await attach_bot(session, meeting.id, bot_id=payload.session_id)
                logger.info(
                    "readai matched session_id=%s to meeting_session=%s by meeting_url",
                    payload.session_id, meeting.id,
                )
        if meeting is None and payload.start_time:
            # Fallback: scheduled_at +/- 30 min window. Risk: two meetings
            # within the window collide. Only triggers when meeting_url absent.
            try:
                from datetime import datetime as _dt
                start_dt = _dt.fromisoformat(payload.start_time.replace("Z", "+00:00"))
                meeting = await get_by_time_window(session, start_time=start_dt, window_minutes=30)
            except Exception as exc:  # noqa: BLE001
                logger.warning("readai start_time parse failed: %s", exc)
                meeting = None
            if meeting is not None:
                # Stash the Read.ai session_id so subsequent webhook deliveries
                # for this report (idempotent retries) match via get_by_bot_id.
                await attach_bot(session, meeting.id, bot_id=payload.session_id)
                logger.info(
                    "readai matched session_id=%s to meeting_session=%s by time window",
                    payload.session_id, meeting.id,
                )
        if meeting is None:
            # Test ping or meeting outside our pipeline. Ack with 200 so
            # the Read.ai dashboard marks delivery healthy.
            logger.warning(
                "readai webhook for unmatched session_id=%s title=%r start=%s -- acking",
                payload.session_id, payload.title, payload.start_time,
            )
            return {"ok": True, "skipped": "unmatched_meeting"}
        meeting_session_id = meeting.id
        application_id = meeting.application_id
        round_value = meeting.round
        existing_transcript_key = meeting.transcript_r2_key

    if existing_transcript_key:
        return {"ok": True, "duplicate": True}

    settings = get_settings()
    bucket = settings.r2_bucket_resumes
    provider = get_meeting_bot_provider()

    # Pull transcript. Read.ai Pro plan sends inline `transcript` (speaker_blocks)
    # in the webhook body and does NOT expose the REST API. Read.ai Enterprise
    # adds `transcript_url` + API key. Order: inline -> URL (only if API key) ->
    # poll fallback (only if API key) -> summary-only (graceful degrade).
    has_api_key = bool(settings.read_ai_api_key)
    transcript: list[dict[str, Any]] = []
    try:
        if payload.transcript:
            t = payload.transcript
            if isinstance(t, dict):
                for k in ("speaker_blocks", "transcript", "segments", "utterances"):
                    if isinstance(t.get(k), list):
                        transcript = t[k]
                        break
            elif isinstance(t, list):
                transcript = t
        elif payload.transcript_url and has_api_key:
            raw_transcript = await provider.download_bytes(url=payload.transcript_url)
            try:
                parsed = json.loads(raw_transcript.decode("utf-8"))
            except Exception:  # noqa: BLE001
                parsed = []
            if isinstance(parsed, dict):
                for k in ("transcript", "segments", "utterances"):
                    if isinstance(parsed.get(k), list):
                        parsed = parsed[k]
                        break
            transcript = parsed if isinstance(parsed, list) else []
        elif has_api_key:
            transcript = await provider.fetch_transcript(
                bot_id=payload.session_id or str(meeting_session_id)
            )
        else:
            logger.info(
                "readai webhook: no inline transcript and no API key (Pro plan) -- "
                "falling back to summary-only for session_id=%s",
                payload.session_id,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("readai transcript fetch failed: %s -- continuing with summary only", exc)
        transcript = []

    # Summary-only fallback so analyzer still runs.
    if not transcript and payload.summary:
        transcript = [{
            "speaker": "Read.ai Summary",
            "text": payload.summary,
            "t_start": 0.0,
            "t_end": 0.0,
        }]

    transcript_key = f"meetings/{application_id}/{meeting_session_id}/transcript.json"
    await upload_blob(
        bucket=bucket,
        key=transcript_key,
        content=json.dumps(transcript, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )

    # Recording: Pro plan does not expose recording URL via webhook AND has no
    # API. Skip silently when no API key. Enterprise can fetch.
    recording_key: str | None = None
    rec_url = payload.recording_url
    try:
        if not rec_url and has_api_key and payload.session_id:
            rec_url = await provider.fetch_recording_url(bot_id=payload.session_id)
        if rec_url and has_api_key:
            audio_bytes = await provider.download_bytes(url=rec_url)
            recording_key = f"meetings/{application_id}/{meeting_session_id}/recording.mp4"
            await upload_blob(
                bucket=bucket,
                key=recording_key,
                content=audio_bytes,
                content_type="video/mp4",
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("readai recording capture failed: %s", exc)

    # Derive duration from start_time/end_time (Read.ai always sends both).
    duration_sec: float | None = None
    if payload.start_time and payload.end_time:
        try:
            from datetime import datetime as _dt
            s = _dt.fromisoformat(payload.start_time.replace("Z", "+00:00"))
            e = _dt.fromisoformat(payload.end_time.replace("Z", "+00:00"))
            duration_sec = (e - s).total_seconds()
        except Exception:  # noqa: BLE001
            duration_sec = None
    if duration_sec is None and transcript:
        last = transcript[-1]
        if isinstance(last, dict):
            t_end = last.get("t_end") or last.get("end") or last.get("end_time")
            if isinstance(t_end, (int, float)):
                duration_sec = float(t_end)

    participants_dump = (
        [p.model_dump(exclude_none=True) for p in payload.participants]
        if payload.participants else None
    )

    ended_at = datetime.now(UTC)
    async with session_scope() as session:
        await save_artifacts(
            session,
            meeting_session_id,
            transcript_r2_key=transcript_key,
            recording_r2_key=recording_key,
            duration_sec=duration_sec,
            ended_at=ended_at,
            participants=participants_dump,
        )
        await log_audit(
            session,
            application_id=application_id,
            action="meeting_bot_done",
            actor="agent",
            details={
                "meeting_session_id": str(meeting_session_id),
                "provider": "readai",
                "session_id": payload.session_id,
                "duration_sec": duration_sec,
                "transcript_turns": len(transcript) if transcript else 0,
                "recording_captured": recording_key is not None,
            },
        )

    queued = await enqueue("analyze_meeting", str(meeting_session_id))
    if not queued:
        background.add_task(analyze_meeting, meeting_session_id=meeting_session_id)
    await publish_event(
        application_id,
        event="meeting_done",
        data={
            "meeting_session_id": str(meeting_session_id),
            "round": round_value,
            "duration_sec": duration_sec,
        },
    )
    await _emit_meeting_supervisor_event(application_id, meeting_session_id, round_value, duration_sec, transcript)
    return {"ok": True, "meeting_session_id": str(meeting_session_id)}
