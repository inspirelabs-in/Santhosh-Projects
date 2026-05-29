"""Voice provider abstraction for outbound AI phone screens.

GrabOn standardised on **ElevenLabs Conversational AI** as the single voice
provider. ElevenLabs hosts the agent (STT + LLM + TTS), bridges to a Twilio
phone number for telephony, and posts the full transcript + analysis back to
us via a single ``post_call_transcription`` webhook.

The hiring agent backend creates one ElevenLabs Agent at deploy time
(persistent ``ELEVENLABS_AGENT_ID``) and uses
``conversation_initiation_client_data`` to override the system prompt + first
message + dynamic variables on every outbound call. That keeps each call
fully tailored to the candidate without spawning per-call agents.

Old providers removed: Pipecat self-host, Plivo SIP, Deepgram, separate
ElevenLabs TTS keys. Everything funnels through the single Conversational
AI key.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx

from src.config import get_settings
from src.models.v1 import VoiceQuestion

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceCallSpec:
    """All inputs required to start one outbound conversational call.

    ``mode`` distinguishes the agent's persona + script:

      * ``screening``    -- the original phone-screen flow.
      * ``confirmation`` -- short confirmation call for an interview slot.
        Reads the proposed time, asks yes/no/reschedule, captures any
        alternative time the candidate gives.
    """

    application_id: UUID
    voice_call_id: UUID
    candidate_name: str
    candidate_phone: str
    role_title: str
    company_name: str
    questions: list[VoiceQuestion]
    system_prompt: str
    webhook_url: str  # informational only -- ElevenLabs posts to its global webhook
    max_seconds: int
    mode: str = "screening"  # "screening" | "confirmation"
    extra_dynamic_variables: dict[str, Any] | None = None
    first_message_override: str | None = None


@dataclass(frozen=True)
class VoiceCallHandle:
    provider: str
    provider_call_id: str  # ElevenLabs conversation_id
    raw: dict[str, Any]


class VoiceProvider(Protocol):
    async def create_call(self, spec: VoiceCallSpec) -> VoiceCallHandle: ...
    async def cancel_call(self, provider_call_id: str) -> None: ...


# ---------------------------------------------------------------------------
# ElevenLabs Conversational AI
# ---------------------------------------------------------------------------


class ElevenLabsConvAIProvider:
    """Talks to ElevenLabs Conversational AI's Twilio outbound endpoint.

    Docs (Apr 2026):
      POST /v1/convai/twilio/outbound-call
        body = {
          agent_id,
          agent_phone_number_id,
          to_number,
          conversation_initiation_client_data: {
            agent: {
              prompt: {prompt: "<system prompt>"},
              first_message: "...",
              language: "en",
            },
            dynamic_variables: {...},
          },
        }
        -> {"conversation_id": "...", "callSid": "..."}

    Cancellation: ``DELETE /v1/convai/conversations/{conversation_id}``.
    """

    BASE = "https://api.elevenlabs.io/v1"

    def __init__(
        self,
        *,
        api_key: str,
        agent_id: str,
        phone_number_id: str,
    ) -> None:
        self._api_key = api_key
        self._agent_id = agent_id
        self._phone_number_id = phone_number_id

    def _headers(self) -> dict[str, str]:
        return {
            "xi-api-key": self._api_key,
            "content-type": "application/json",
            "accept": "application/json",
        }

    def _build_initiation(self, spec: VoiceCallSpec) -> dict[str, Any]:
        """Build the per-call override payload."""
        if spec.mode == "confirmation":
            full_prompt = spec.system_prompt
            first_message = spec.first_message_override or (
                f"Hi {spec.candidate_name}, this is the {spec.company_name} hiring assistant. "
                "I'm calling to confirm your upcoming interview."
            )
        else:
            question_lines = []
            for idx, q in enumerate(spec.questions, start=1):
                line = f"{idx}. ({q.id}) {q.question}"
                if q.follow_up_hint:
                    line += f"\n   probe-if-thin: {q.follow_up_hint}"
                question_lines.append(line)
            full_prompt = (
                spec.system_prompt
                + "\n\n--- Question script (ask one at a time, in order) ---\n"
                + "\n".join(question_lines)
                + "\n\n--- Tools ---\n"
                + "Use the end_call tool ONLY per the CRITICAL HANGUP RULES above. "
                + "Never invoke end_call during your own utterance. Always speak the "
                + "closing line first, wait for it to finish, then invoke end_call."
            )
            first_message = spec.first_message_override or (
                f"Hi, this is the AI hiring assistant from {spec.company_name}, "
                f"calling about the {spec.role_title} role you applied for. "
                "Is this a good time to talk?"
            )
        dynamic_vars = {
            "candidate_name": spec.candidate_name,
            "role_title": spec.role_title,
            "company_name": spec.company_name,
            "application_id": str(spec.application_id),
            "voice_call_id": str(spec.voice_call_id),
            "mode": spec.mode,
        }
        if spec.extra_dynamic_variables:
            dynamic_vars.update({k: str(v) for k, v in spec.extra_dynamic_variables.items()})
        # ElevenLabs requires per-call prompt/first_message overrides to live
        # under `conversation_config_override.agent`. Without this wrapper the
        # Agent silently falls back to its dashboard-configured greeting (e.g.
        # "Hello, I am your recruitment assistant"), ignoring our copy.
        # Per-call call-duration + turn settings.
        # Allowed override fields require the matching "Security > Override"
        # toggles enabled on the ElevenLabs Agent. If a tenant hasn't enabled
        # them the API returns 400 -- we strip them and retry, see create_call.
        return {
            "conversation_config_override": {
                "agent": {
                    "prompt": {"prompt": full_prompt},
                    "first_message": first_message,
                    "language": "en",
                },
                "conversation": {
                    "max_duration_seconds": int(spec.max_seconds),
                },
                "turn": {
                    # Wait at least 8s of silence before letting the agent
                    # decide to speak again. Prevents premature end-of-turn
                    # detection from killing the call mid-greeting on slow
                    # networks.
                    "silence_end_call_timeout_secs": 30,
                    "turn_timeout": 12,
                },
            },
            "dynamic_variables": dynamic_vars,
        }

    async def create_call(self, spec: VoiceCallSpec) -> VoiceCallHandle:
        from src.services.circuit_breaker import ELEVENLABS, check_circuit, record_failure, record_success

        await check_circuit(ELEVENLABS)

        initiation = self._build_initiation(spec)
        payload = {
            "agent_id": self._agent_id,
            "agent_phone_number_id": self._phone_number_id,
            "to_number": spec.candidate_phone,
            "conversation_initiation_client_data": initiation,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.BASE}/convai/twilio/outbound-call",
                json=payload,
                headers=self._headers(),
            )
            # If ElevenLabs rejects the conversation/turn overrides because the
            # agent's "Security > Override" toggles are off, retry with only
            # the agent.{prompt,first_message,language} fields. Without this
            # fallback any tenant with locked-down overrides gets a 400 and
            # the call never dispatches.
            if resp.status_code == 400 and (
                "conversation" in resp.text or "turn" in resp.text or "override" in resp.text.lower()
            ):
                logger.warning(
                    "elevenlabs rejected conversation/turn override (%s); retrying with agent-only override",
                    resp.text[:200],
                )
                stripped = dict(initiation)
                stripped["conversation_config_override"] = {
                    "agent": initiation["conversation_config_override"]["agent"]
                }
                payload["conversation_initiation_client_data"] = stripped
                resp = await client.post(
                    f"{self.BASE}/convai/twilio/outbound-call",
                    json=payload,
                    headers=self._headers(),
                )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                await record_failure(ELEVENLABS, f"HTTP {exc.response.status_code}")
                raise
            await record_success(ELEVENLABS)
            data = resp.json()
        if data.get("success") is False:
            msg = data.get("message") or "unknown error"
            raise RuntimeError(f"ElevenLabs convai call failed: {msg}")
        conversation_id = (
            data.get("conversation_id")
            or data.get("conversationId")
            or data.get("conversation", {}).get("id")
        )
        if not conversation_id:
            logger.error("ElevenLabs convai response missing conversation_id: %s", data)
            raise RuntimeError(
                f"ElevenLabs convai response missing conversation_id: {list(data)}"
            )
        return VoiceCallHandle(
            provider="elevenlabs",
            provider_call_id=str(conversation_id),
            raw=data,
        )

    async def cancel_call(self, provider_call_id: str) -> None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.delete(
                    f"{self.BASE}/convai/conversations/{provider_call_id}",
                    headers=self._headers(),
                )
                if resp.status_code not in (200, 202, 204, 404):
                    resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning(
                    "elevenlabs cancel failed for %s: %s", provider_call_id, exc
                )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_provider_singleton: VoiceProvider | None = None


def get_voice_provider() -> VoiceProvider:
    global _provider_singleton
    if _provider_singleton is not None:
        return _provider_singleton
    settings = get_settings()
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not configured")
    if not settings.elevenlabs_agent_id:
        raise RuntimeError("ELEVENLABS_AGENT_ID not configured")
    if not settings.elevenlabs_phone_number_id:
        raise RuntimeError("ELEVENLABS_PHONE_NUMBER_ID not configured")
    _provider_singleton = ElevenLabsConvAIProvider(
        api_key=settings.elevenlabs_api_key,
        agent_id=settings.elevenlabs_agent_id,
        phone_number_id=settings.elevenlabs_phone_number_id,
    )
    return _provider_singleton


def reset_voice_provider() -> None:
    global _provider_singleton
    _provider_singleton = None
