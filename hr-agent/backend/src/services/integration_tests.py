"""Live test-connection probes used by the admin Settings UI.

Each probe takes the candidate config dict (about-to-be-saved values, NOT
the persisted ones) and returns a `ProbeResult`. Probes are best-effort:
they should never raise and should always finish in <10s.

Used by `POST /admin/config/test/{integration}`. The admin UI wires a
"Test connection" button next to each integration so HR can paste a key
and verify it before clicking Save.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 10.0


@dataclass
class ProbeResult:
    ok: bool
    message: str
    detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "detail": self.detail or {}}


def _ok(msg: str, **detail: Any) -> ProbeResult:
    return ProbeResult(True, msg, detail or None)


def _err(msg: str, **detail: Any) -> ProbeResult:
    return ProbeResult(False, msg, detail or None)


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------


async def probe_openai(cfg: dict[str, Any]) -> ProbeResult:
    key = cfg.get("OPENAI_API_KEY") or ""
    if not key:
        return _err("Missing OPENAI_API_KEY")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        try:
            r = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            if r.status_code == 200:
                models = [m["id"] for m in r.json().get("data", [])][:5]
                return _ok("OpenAI key valid", models=models)
            return _err(f"OpenAI rejected key: HTTP {r.status_code}", body=r.text[:200])
        except Exception as exc:  # noqa: BLE001
            return _err(f"OpenAI probe error: {exc}")


async def probe_anthropic(cfg: dict[str, Any]) -> ProbeResult:
    key = cfg.get("ANTHROPIC_API_KEY") or ""
    if not key:
        return _err("Missing ANTHROPIC_API_KEY")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        try:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5",
                    "max_tokens": 8,
                    "messages": [{"role": "user", "content": "ping"}],
                },
            )
            if r.status_code == 200:
                return _ok("Anthropic key valid")
            return _err(f"Anthropic rejected key: HTTP {r.status_code}", body=r.text[:200])
        except Exception as exc:  # noqa: BLE001
            return _err(f"Anthropic probe error: {exc}")


async def probe_groq(cfg: dict[str, Any]) -> ProbeResult:
    key = cfg.get("GROQ_API_KEY") or ""
    if not key:
        return _err("Missing GROQ_API_KEY")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        try:
            r = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            if r.status_code == 200:
                return _ok("Groq key valid")
            return _err(f"Groq rejected key: HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            return _err(f"Groq probe error: {exc}")


async def probe_resend(cfg: dict[str, Any]) -> ProbeResult:
    key = cfg.get("RESEND_API_KEY") or ""
    if not key:
        return _err("Missing RESEND_API_KEY")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        try:
            r = await client.get(
                "https://api.resend.com/domains",
                headers={"Authorization": f"Bearer {key}"},
            )
            if r.status_code == 200:
                domains = [d.get("name") for d in r.json().get("data", [])]
                return _ok("Resend key valid", domains=domains)
            return _err(f"Resend rejected key: HTTP {r.status_code}", body=r.text[:200])
        except Exception as exc:  # noqa: BLE001
            return _err(f"Resend probe error: {exc}")


async def probe_recall(cfg: dict[str, Any]) -> ProbeResult:
    key = cfg.get("RECALL_AI_API_KEY") or ""
    base = cfg.get("RECALL_AI_BASE_URL") or "https://us-east-1.recall.ai/api/v1"
    if not key:
        return _err("Missing RECALL_AI_API_KEY")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        try:
            r = await client.get(
                f"{base.rstrip('/')}/bot",
                headers={"Authorization": f"Token {key}"},
                params={"page_size": 1},
            )
            if r.status_code in (200, 204):
                return _ok("Recall.ai key valid")
            return _err(f"Recall.ai rejected key: HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            return _err(f"Recall.ai probe error: {exc}")


async def probe_graph(cfg: dict[str, Any]) -> ProbeResult:
    tenant = cfg.get("GRAPH_TENANT_ID") or ""
    client_id = cfg.get("GRAPH_CLIENT_ID") or ""
    secret = cfg.get("GRAPH_CLIENT_SECRET") or ""
    if not (tenant and client_id and secret):
        return _err("Need tenant id, client id, and client secret")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        try:
            r = await client.post(
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": secret,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )
            if r.status_code == 200 and r.json().get("access_token"):
                return _ok("MS Graph token acquired")
            return _err(
                f"Graph token request failed: HTTP {r.status_code}",
                body=r.text[:200],
            )
        except Exception as exc:  # noqa: BLE001
            return _err(f"Graph probe error: {exc}")


async def probe_msg91(cfg: dict[str, Any]) -> ProbeResult:
    key = cfg.get("MSG91_AUTH_KEY") or ""
    if not key:
        return _err("Missing MSG91_AUTH_KEY")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        try:
            r = await client.get(
                "https://control.msg91.com/api/balance.php",
                headers={"authkey": key},
                params={"type": "4"},
            )
            if r.status_code == 200 and "ERROR" not in r.text.upper():
                return _ok("MSG91 key valid", balance=r.text.strip()[:64])
            return _err(f"MSG91 rejected key: HTTP {r.status_code}", body=r.text[:200])
        except Exception as exc:  # noqa: BLE001
            return _err(f"MSG91 probe error: {exc}")


async def probe_elevenlabs(cfg: dict[str, Any]) -> ProbeResult:
    key = cfg.get("ELEVENLABS_API_KEY") or ""
    if not key:
        return _err("Missing ELEVENLABS_API_KEY")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        try:
            r = await client.get(
                "https://api.elevenlabs.io/v1/user",
                headers={"xi-api-key": key},
            )
            if r.status_code == 200:
                user = r.json()
                return _ok("ElevenLabs key valid", user=user.get("subscription", {}).get("tier"))
            return _err(f"ElevenLabs rejected key: HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            return _err(f"ElevenLabs probe error: {exc}")


async def probe_whatsapp(cfg: dict[str, Any]) -> ProbeResult:
    token = cfg.get("WHATSAPP_ACCESS_TOKEN") or ""
    phone = cfg.get("WHATSAPP_PHONE_NUMBER_ID") or ""
    if not (token and phone):
        return _err("Need access token and phone number id")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        try:
            r = await client.get(
                f"https://graph.facebook.com/v20.0/{phone}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200:
                return _ok("WhatsApp Cloud API reachable", display=r.json().get("display_phone_number"))
            return _err(f"WhatsApp rejected: HTTP {r.status_code}", body=r.text[:200])
        except Exception as exc:  # noqa: BLE001
            return _err(f"WhatsApp probe error: {exc}")


async def probe_r2(cfg: dict[str, Any]) -> ProbeResult:
    """Head-bucket against the configured object store."""
    from src.config import get_settings

    s = get_settings()
    key_id = cfg.get("R2_ACCESS_KEY_ID") or s.r2_access_key_id
    secret = cfg.get("R2_SECRET_ACCESS_KEY") or s.r2_secret_access_key
    bucket = cfg.get("R2_BUCKET_RESUMES") or s.r2_bucket_resumes
    endpoint = s.r2_endpoint_url
    if not (key_id and secret and bucket and endpoint):
        return _err("Need access key id, secret, bucket, and (env) endpoint")
    try:
        import boto3
        from botocore.client import Config

        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            region_name=s.r2_region or "auto",
            config=Config(signature_version="s3v4"),
        )
        await asyncio.to_thread(client.head_bucket, Bucket=bucket)
        return _ok(f"Bucket '{bucket}' reachable")
    except Exception as exc:  # noqa: BLE001
        return _err(f"R2 probe error: {exc}")


async def probe_smtp(cfg: dict[str, Any]) -> ProbeResult:
    host = cfg.get("SMTP_HOST") or ""
    port = int(cfg.get("SMTP_PORT") or 0)
    use_tls = bool(cfg.get("SMTP_USE_TLS"))
    if not (host and port):
        return _err("Need SMTP host and port")
    try:
        import aiosmtplib

        smtp = aiosmtplib.SMTP(hostname=host, port=port, timeout=PROBE_TIMEOUT, use_tls=use_tls)
        await smtp.connect()
        await smtp.noop()
        await smtp.quit()
        return _ok(f"SMTP {host}:{port} reachable")
    except Exception as exc:  # noqa: BLE001
        return _err(f"SMTP probe error: {exc}")


async def probe_teams_hr(cfg: dict[str, Any]) -> ProbeResult:
    return await _probe_teams_webhook(cfg.get("TEAMS_WEBHOOK_HR"))


async def probe_teams_alerts(cfg: dict[str, Any]) -> ProbeResult:
    return await _probe_teams_webhook(cfg.get("TEAMS_WEBHOOK_ALERTS"))


async def _probe_teams_webhook(url: str | None) -> ProbeResult:
    if not url:
        return _err("Webhook URL empty")
    if not url.startswith(("http://", "https://")):
        return _err("Webhook URL must be http(s)")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        try:
            r = await client.post(url, json={"text": "Hiring Agent — config test ping"})
            if 200 <= r.status_code < 300:
                return _ok("Teams webhook accepted")
            return _err(f"Teams webhook rejected: HTTP {r.status_code}", body=r.text[:200])
        except Exception as exc:  # noqa: BLE001
            return _err(f"Teams probe error: {exc}")


async def probe_emotion(cfg: dict[str, Any]) -> ProbeResult:
    endpoint = cfg.get("EMOTION_MODEL_ENDPOINT") or ""
    api_key = cfg.get("EMOTION_API_KEY") or ""
    if not endpoint:
        return _err("Missing EMOTION_MODEL_ENDPOINT")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            r = await client.get(f"{endpoint.rstrip('/')}/healthz", headers=headers)
            if r.status_code == 200:
                return _ok("Emotion service healthy")
            return _err(f"Emotion service unhealthy: HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            return _err(f"Emotion probe error: {exc}")


async def probe_calcom(cfg: dict[str, Any]) -> ProbeResult:
    key = cfg.get("CALCOM_API_KEY") or ""
    base = cfg.get("CALCOM_BASE_URL") or "https://cal.com"
    if not key:
        return _err("Missing CALCOM_API_KEY")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        try:
            r = await client.get(f"{base.rstrip('/')}/api/v1/me", params={"apiKey": key})
            if r.status_code == 200:
                return _ok("Cal.com key valid")
            return _err(f"Cal.com rejected: HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            return _err(f"Cal.com probe error: {exc}")


async def probe_google_calendar(cfg: dict[str, Any]) -> ProbeResult:
    raw = cfg.get("GOOGLE_CALENDAR_SERVICE_ACCOUNT_JSON") or ""
    if not raw:
        return _err("Missing service account JSON")
    try:
        import json as _json

        creds = _json.loads(raw)
        if "client_email" not in creds:
            return _err("Service account JSON missing client_email")
        return _ok("Service account JSON parsed", client_email=creds.get("client_email"))
    except Exception as exc:  # noqa: BLE001
        return _err(f"Service account JSON invalid: {exc}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PROBES: dict[str, Callable[[dict[str, Any]], Awaitable[ProbeResult]]] = {
    "openai": probe_openai,
    "anthropic": probe_anthropic,
    "groq": probe_groq,
    "llm": probe_openai,  # default LLM probe
    "resend": probe_resend,
    "smtp": probe_smtp,
    "recall": probe_recall,
    "graph": probe_graph,
    "msg91": probe_msg91,
    "elevenlabs": probe_elevenlabs,
    "whatsapp": probe_whatsapp,
    "r2": probe_r2,
    "teams_hr": probe_teams_hr,
    "teams_alerts": probe_teams_alerts,
    "emotion": probe_emotion,
    "calcom": probe_calcom,
    "google_calendar": probe_google_calendar,
}


async def run_probe(integration: str, cfg: dict[str, Any]) -> ProbeResult:
    fn = PROBES.get(integration)
    if not fn:
        return _err(f"Unknown integration '{integration}'")
    try:
        return await asyncio.wait_for(fn(cfg), timeout=PROBE_TIMEOUT + 2)
    except asyncio.TimeoutError:
        return _err(f"Probe timed out after {PROBE_TIMEOUT}s")
    except Exception as exc:  # noqa: BLE001
        return _err(f"Probe crashed: {exc}")
