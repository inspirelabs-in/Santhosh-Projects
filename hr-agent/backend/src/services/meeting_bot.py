"""Meeting-bot client for capturing Teams technical + CEO interviews.

Two providers supported:

* **Read.ai** (default, ``meeting_bot_provider == "readai"``).
  Read.ai joins meetings through its native calendar OAuth integration
  with the configured M365 / Google organiser mailbox -- there is *no*
  programmatic ``POST /bot`` endpoint. ``dispatch_bot`` therefore returns
  a synthetic bot id (== meeting_session_id). Completed transcripts arrive
  via Read.ai webhook (``meeting.completed``) into
  ``/webhooks/meeting/readai``; a fallback poller hits
  ``GET {base}/meetings`` to reconcile missed webhook deliveries.

* **Recall.ai** (legacy). Programmatic bot dispatch + ``bot.done`` webhook.

Contract every provider must satisfy:

  * ``dispatch_bot(...)``        -> ``BotHandle`` (bot_id used to correlate later)
  * ``fetch_transcript(bot_id)`` -> diarized list[dict]
  * ``fetch_recording_url(bot_id)`` -> str | None
  * ``download_bytes(url)``      -> bytes
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotHandle:
    bot_id: str
    raw: dict[str, Any]


class MeetingBotProvider(Protocol):
    async def dispatch_bot(
        self,
        *,
        meeting_session_id: UUID,
        join_url: str,
        scheduled_at: str | None,
        webhook_url: str,
        display_name: str,
    ) -> BotHandle: ...

    async def fetch_transcript(self, *, bot_id: str) -> list[dict[str, Any]]: ...

    async def fetch_recording_url(self, *, bot_id: str) -> str | None: ...

    async def download_bytes(self, *, url: str) -> bytes: ...


# ---------------------------------------------------------------------------
# Recall.ai client
# ---------------------------------------------------------------------------


class RecallClient:
    def __init__(self, *, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Token {self._api_key}",
            "content-type": "application/json",
            "accept": "application/json",
        }

    async def dispatch_bot(
        self,
        *,
        meeting_session_id: UUID,
        join_url: str,
        scheduled_at: str | None,
        webhook_url: str,
        display_name: str,
    ) -> BotHandle:
        payload: dict[str, Any] = {
            "meeting_url": join_url,
            "bot_name": display_name,
            "metadata": {"meeting_session_id": str(meeting_session_id)},
            "transcription_options": {"provider": "deepgram"},
            "real_time_transcription": {
                "destination_url": webhook_url,
                "partial_results": False,
            },
            "automatic_leave": {
                "waiting_room_timeout": 600,
                "noone_joined_timeout": 600,
                "everyone_left_timeout": 60,
            },
        }
        if scheduled_at is not None:
            payload["join_at"] = scheduled_at

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base_url}/bot", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            data = resp.json()
        bot_id = data.get("id") or data.get("bot_id")
        if not bot_id:
            raise RuntimeError(f"Recall did not return bot id: {list(data)}")
        return BotHandle(bot_id=str(bot_id), raw=data)

    async def fetch_transcript(self, *, bot_id: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{self._base_url}/bot/{bot_id}/transcript",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "transcript" in data:
            return list(data["transcript"])
        return []

    async def fetch_recording_url(self, *, bot_id: str) -> str | None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._base_url}/bot/{bot_id}", headers=self._headers()
            )
            resp.raise_for_status()
            data = resp.json()
        # Recall.ai returns recording info under several possible keys.
        for key in ("video_url", "recording_url", "media_url"):
            url = data.get(key)
            if url:
                return str(url)
        media = data.get("recordings") or []
        if isinstance(media, list) and media:
            url = media[0].get("download_url") or media[0].get("media_url")
            if url:
                return str(url)
        return None

    async def download_bytes(self, *, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content


# ---------------------------------------------------------------------------
# Read.ai client (calendar-trigger; no on-demand bot dispatch).
# ---------------------------------------------------------------------------


class ReadAIClient:
    """Calendar-driven provider.

    ``dispatch_bot`` does NOT call Read.ai -- it just mints a synthetic
    bot id (the meeting_session_id) used later to correlate the inbound
    webhook / polled report. The actual joining happens because the
    meeting was created on the Read.ai-connected organiser mailbox.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        organiser_email: str | None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._organiser = organiser_email

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._api_key}" if self._api_key else "",
            "accept": "application/json",
        }

    async def dispatch_bot(
        self,
        *,
        meeting_session_id: UUID,
        join_url: str,
        scheduled_at: str | None,
        webhook_url: str,
        display_name: str,
    ) -> BotHandle:
        # No HTTP call -- Read.ai joins via calendar OAuth.
        synthetic_id = str(meeting_session_id)
        logger.info(
            "Read.ai dispatch (calendar-trigger) for meeting_session=%s join_url=%s scheduled=%s",
            synthetic_id, join_url, scheduled_at,
        )
        return BotHandle(
            bot_id=synthetic_id,
            raw={
                "provider": "readai",
                "calendar_trigger": True,
                "organiser": self._organiser,
                "join_url": join_url,
                "scheduled_at": scheduled_at,
            },
        )

    async def find_report(
        self,
        *,
        join_url: str | None = None,
        scheduled_at_iso: str | None = None,
    ) -> dict[str, Any] | None:
        """Hit Read.ai REST and return the matching report dict, or None."""
        if not self._api_key:
            raise RuntimeError("READ_AI_API_KEY not configured")
        params: dict[str, str] = {}
        if scheduled_at_iso:
            params["start_time"] = scheduled_at_iso
        if self._organiser:
            params["organiser"] = self._organiser
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._base_url}/meetings", headers=self._headers(), params=params
            )
            resp.raise_for_status()
            data = resp.json()
        items = data.get("meetings") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return None
        if join_url:
            for it in items:
                if isinstance(it, dict) and it.get("meeting_url") == join_url:
                    return it
        return items[0] if items else None

    async def fetch_transcript(self, *, bot_id: str) -> list[dict[str, Any]]:
        """For Read.ai, ``bot_id`` is the meeting_session_id (synthetic).
        The webhook handler resolves the actual Read.ai report id and
        passes the transcript JSON through ``download_bytes`` instead.
        This method is only invoked when polling -- it expects the report
        id to have been stashed via ``find_report`` first.
        """
        if not self._api_key:
            raise RuntimeError("READ_AI_API_KEY not configured")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{self._base_url}/meetings/{bot_id}/transcript",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("transcript", "segments", "utterances"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        return []

    async def fetch_recording_url(self, *, bot_id: str) -> str | None:
        if not self._api_key:
            return None
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._base_url}/meetings/{bot_id}", headers=self._headers()
            )
            if resp.status_code >= 400:
                return None
            data = resp.json()
        for key in ("recording_url", "video_url", "media_url"):
            url = data.get(key)
            if url:
                return str(url)
        return None

    async def download_bytes(self, *, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(url, headers=self._headers())
            resp.raise_for_status()
            return resp.content


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_singleton: MeetingBotProvider | None = None


def get_meeting_bot_provider() -> MeetingBotProvider:
    global _singleton
    if _singleton is not None:
        return _singleton
    settings = get_settings()
    provider = settings.meeting_bot_provider
    if provider == "readai":
        _singleton = ReadAIClient(
            api_key=settings.read_ai_api_key,
            base_url=settings.read_ai_base_url,
            organiser_email=settings.read_ai_organiser_email or settings.graph_organiser_email,
        )
        return _singleton
    if provider == "recall":
        if not settings.recall_ai_api_key:
            raise RuntimeError("RECALL_AI_API_KEY not configured")
        _singleton = RecallClient(
            api_key=settings.recall_ai_api_key, base_url=settings.recall_ai_base_url
        )
        return _singleton
    raise RuntimeError(f"meeting_bot_provider {provider!r} not implemented")


def reset_meeting_bot_provider() -> None:
    global _singleton
    _singleton = None
