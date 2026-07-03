"""Outbound transactional email.

Three providers supported, selected via ``settings.email_provider``
(default ``"auto"`` picks the first configured in priority order):

  1. Microsoft Graph ``/users/{id}/sendMail`` — preferred for M365 tenants.
     Reuses the same Azure AD app (graph_client_id / graph_tenant_id /
     graph_client_secret) registered for Teams scheduling. Requires the
     ``Mail.Send`` application permission on that app, granted admin
     consent. The sending mailbox is taken from
     ``graph_organiser_email`` unless overridden per-template.
  2. Resend (HTTPS API) — legacy / non-M365 fallback.
  3. SMTP (aiosmtplib) — MailHog locally (1025), Outlook
     ``smtp.office365.com:587`` STARTTLS in prod, or any other SMTP relay.

If none configured the send is skipped and logged.
"""

from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import aiosmtplib
import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.config import get_settings
from src.constants.external import MS_GRAPH_API_BASE, MS_GRAPH_DEFAULT_SCOPE, MS_GRAPH_TOKEN_URL_TEMPLATE

logger = logging.getLogger(__name__)
_settings = get_settings()

_RESEND_API = "https://api.resend.com/emails"
_GRAPH_API = MS_GRAPH_API_BASE
_GRAPH_TOKEN_URL = MS_GRAPH_TOKEN_URL_TEMPLATE
_TEMPLATES_DIR = Path(__file__).parent / "templates" / "email"

# Cached Graph access token. Tuple of (token, epoch_expiry).
_graph_token_cache: tuple[str, float] | None = None


@dataclass
class EmailAttachment:
    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


@dataclass
class EmailSendResult:
    success: bool
    message_id: str | None
    status_code: int
    error: str | None = None
    provider: str = "none"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        enable_async=False,
    )


def _brand_context() -> dict[str, Any]:
    """Brand tokens injected into every email render.

    Sourced from https://www.grabon.in/branding/. Templates pull these via
    ``{{ brand.* }}`` so palette + logo URL change in one place.
    """
    return {
        "brand": {
            "company_name": "GrabOn",
            "tagline": "agentic hiring",
            "logo_url": "https://cdn.grabon.in/gograbon/logo/GrabOn-Logo.svg",
            "logo_dark_url": "https://cdn.grabon.in/gograbon/logo/GrabOn-Logo-Light.svg",
            "primary": "#60A600",        # GrabOn green
            "primary_light": "#D1DE31",  # GrabOn green light
            
            "ink": "#061A38",            # deep sea blue
            "info": "#2491EF",           # bright blue
            "surface": "#F7F7F8",        # light gray
            "border": "#E2E5EC",
            "muted_text": "#3F4861",
            "font_stack": "'Nunito Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
            "google_font_url": "https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@400;600;700;800&display=swap",
            "careers_email": "careers@grabon.in",
            "privacy_email": "privacy@grabon.in",
        }
    }


def render_template(template_name: str, variables: dict[str, Any]) -> tuple[str, str]:
    env = _jinja_env()
    tmpl = env.get_template(f"{template_name}.html.j2")
    merged = {**_brand_context(), **variables}
    module = tmpl.make_module(merged)
    subject = str(getattr(module, "subject", "")).strip()
    body_html = tmpl.render(merged)
    return subject, body_html


async def _graph_access_token() -> str:
    global _graph_token_cache
    import time

    now = time.time()
    if _graph_token_cache and _graph_token_cache[1] - 60 > now:
        return _graph_token_cache[0]

    if not (_settings.graph_tenant_id and _settings.graph_client_id and _settings.graph_client_secret):
        raise RuntimeError("Graph credentials missing for email send")

    token_url = _GRAPH_TOKEN_URL.format(tenant=_settings.graph_tenant_id)
    data = {
        "client_id": _settings.graph_client_id,
        "client_secret": _settings.graph_client_secret,
        "scope": MS_GRAPH_DEFAULT_SCOPE,
        "grant_type": "client_credentials",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(token_url, data=data)
        resp.raise_for_status()
        body = resp.json()
    token = body["access_token"]
    expires_in = float(body.get("expires_in", 3600))
    _graph_token_cache = (token, now + expires_in)
    return token


def _parse_address(header: str) -> tuple[str, str]:
    """Split ``"Display Name <addr@x>"`` -> (name, addr). If no angle
    brackets, the whole string becomes the address and name is empty."""
    if "<" in header and ">" in header:
        name = header.split("<", 1)[0].strip().strip('"')
        addr = header.split("<", 1)[1].split(">", 1)[0].strip()
        return name, addr
    return "", header.strip()


async def _send_via_graph(
    *,
    to: str,
    subject: str,
    html: str,
    from_header: str,
    reply_to: str,
    attachments: list[EmailAttachment] | None = None,
) -> EmailSendResult:
    from_name, from_addr = _parse_address(from_header)
    sender_mailbox = from_addr or _settings.graph_organiser_email
    if not sender_mailbox:
        return EmailSendResult(
            success=False,
            message_id=None,
            status_code=0,
            error="graph: no sender mailbox (set graph_organiser_email or pass from_email)",
            provider="graph",
        )

    message: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html},
        "toRecipients": [{"emailAddress": {"address": to}}],
        "from": {"emailAddress": {"address": from_addr, "name": from_name or from_addr}},
        "replyTo": [{"emailAddress": {"address": reply_to}}] if reply_to else [],
    }
    if attachments:
        message["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": a.filename,
                "contentType": a.content_type,
                "contentBytes": base64.b64encode(a.content).decode("ascii"),
            }
            for a in attachments
        ]

    payload = {"message": message, "saveToSentItems": True}

    try:
        token = await _graph_access_token()
    except Exception as e:  # noqa: BLE001
        logger.error("Graph token fetch failed: %s", e)
        return EmailSendResult(
            success=False, message_id=None, status_code=0, error=str(e)[:500], provider="graph"
        )

    url = f"{_GRAPH_API}/users/{sender_mailbox}/sendMail"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, json=payload, headers=headers)

    # 202 Accepted is the success code for sendMail.
    if resp.status_code >= 400:
        logger.error("Graph sendMail %s: %s", resp.status_code, resp.text[:300])
        return EmailSendResult(
            success=False,
            message_id=None,
            status_code=resp.status_code,
            error=resp.text[:500],
            provider="graph",
        )
    msg_id = f"<{uuid.uuid4()}@graph-sendmail>"
    return EmailSendResult(
        success=True, message_id=msg_id, status_code=resp.status_code, provider="graph"
    )


async def _send_via_resend(
    *,
    to: str,
    subject: str,
    html: str,
    from_header: str,
    reply_to: str,
    tags: dict[str, str] | None,
    attachments: list[EmailAttachment] | None = None,
) -> EmailSendResult:
    payload: dict[str, Any] = {
        "from": from_header,
        "to": [to],
        "subject": subject,
        "html": html,
        "reply_to": reply_to,
    }
    if tags:
        payload["tags"] = [{"name": k, "value": v} for k, v in tags.items()]
    if attachments:
        payload["attachments"] = [
            {
                "filename": a.filename,
                "content": base64.b64encode(a.content).decode("ascii"),
                "content_type": a.content_type,
            }
            for a in attachments
        ]

    headers = {
        "Authorization": f"Bearer {_settings.resend_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(_RESEND_API, json=payload, headers=headers)

    if resp.status_code >= 400:
        logger.error("Resend error %s: %s", resp.status_code, resp.text[:200])
        return EmailSendResult(
            success=False,
            message_id=None,
            status_code=resp.status_code,
            error=resp.text[:500],
            provider="resend",
        )
    data = resp.json()
    return EmailSendResult(
        success=True,
        message_id=data.get("id"),
        status_code=resp.status_code,
        provider="resend",
    )


async def _send_via_smtp(
    *,
    to: str,
    subject: str,
    html: str,
    from_header: str,
    reply_to: str,
    attachments: list[EmailAttachment] | None = None,
) -> EmailSendResult:
    msg = EmailMessage()
    msg["From"] = from_header
    msg["To"] = to
    msg["Subject"] = subject
    msg["Reply-To"] = reply_to
    msg_id = f"<{uuid.uuid4()}@hiring-agent>"
    msg["Message-Id"] = msg_id
    # Short plain-text fallback so servers don't reject HTML-only messages.
    msg.set_content("This email is best viewed in an HTML-capable client.")
    msg.add_alternative(html, subtype="html")
    if attachments:
        for a in attachments:
            maintype, _, subtype = (a.content_type or "application/octet-stream").partition("/")
            msg.add_attachment(
                a.content,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=a.filename,
            )

    try:
        await aiosmtplib.send(
            msg,
            hostname=_settings.smtp_host,
            port=_settings.smtp_port,
            username=_settings.smtp_user or None,
            password=_settings.smtp_password or None,
            use_tls=_settings.smtp_use_tls and _settings.smtp_port == 465,
            start_tls=_settings.smtp_use_tls and _settings.smtp_port != 465,
            timeout=15.0,
        )
    except Exception as e:  # noqa: BLE001 -- log + return result
        logger.error("SMTP send failed: %s", e)
        return EmailSendResult(
            success=False, message_id=None, status_code=0, error=str(e)[:500], provider="smtp"
        )

    return EmailSendResult(success=True, message_id=msg_id, status_code=250, provider="smtp")


_FROM_BY_TEMPLATE: dict[str, tuple[str | None, str | None]] = {
    # Technical panel-facing
    "tech_review_request": ("email_from_technical", "GrabOn Engineering"),
    # CEO-facing
    "ceo_handoff": ("email_from_ceo", "GrabOn CEO Office"),
    # HR-facing
    "hr_review_request": ("email_from_hr", "GrabOn HR"),
    "offer_extended": ("email_from_hr", "GrabOn HR"),
    # Candidate-facing default to careers@ — fall through to resend defaults
}


def _resolve_from(template: str, override: str | None, override_name: str | None) -> tuple[str, str]:
    """Pick the From email + name. Explicit override wins; else look up by
    template; else fall back to the global careers@ address."""
    if override:
        return override, override_name or _settings.resend_from_name
    setting_attr, default_name = _FROM_BY_TEMPLATE.get(template, (None, None))
    if setting_attr:
        addr = getattr(_settings, setting_attr, None)
        if addr:
            return addr, override_name or default_name or _settings.resend_from_name
    return _settings.resend_from_email, override_name or _settings.resend_from_name


async def send_email(
    *,
    to: str,
    template: str,
    variables: dict[str, Any],
    from_email: str | None = None,
    from_name: str | None = None,
    reply_to: str | None = None,
    tags: dict[str, str] | None = None,
    attachments: list[EmailAttachment] | None = None,
    idempotency_key: str | None = None,
    application_id: str | None = None,
    candidate_id: str | None = None,
) -> EmailSendResult:
    # Idempotent send: if a row already exists for this key with status=sent,
    # skip the network call. Caller pattern: pass a stable key like
    # f"{application_id}:{template}:{round}" to dedupe retried sends.
    if idempotency_key:
        existing = await _email_send_lookup(idempotency_key)
        if existing is not None:
            logger.info(
                "send_email idempotent skip key=%s template=%s prior_provider=%s",
                idempotency_key, template, existing.get("provider"),
            )
            return EmailSendResult(
                success=existing.get("status") == "sent",
                message_id=existing.get("provider_message_id"),
                status_code=200,
                provider=existing.get("provider", "cached"),
                error=existing.get("error"),
            )

    subject, html = render_template(template, variables)
    resolved_email, resolved_name = _resolve_from(template, from_email, from_name)
    from_header = f"{resolved_name} <{resolved_email}>"
    reply_to_final = reply_to or resolved_email or _settings.resend_reply_to

    provider = (_settings.email_provider or "auto").lower()
    graph_ready = bool(
        _settings.graph_tenant_id
        and _settings.graph_client_id
        and _settings.graph_client_secret
        and (_settings.graph_organiser_email or _resolve_from(template, from_email, from_name)[0])
    )

    result: EmailSendResult
    if provider == "graph" or (provider == "auto" and graph_ready):
        result = await _send_via_graph(
            to=to,
            subject=subject,
            html=html,
            from_header=from_header,
            reply_to=reply_to_final,
            attachments=attachments,
        )
        await _email_send_record(
            idempotency_key=idempotency_key, to=to, template=template,
            application_id=application_id, candidate_id=candidate_id, result=result,
        )
        return result

    if provider == "resend" or (provider == "auto" and _settings.resend_api_key):
        result = await _send_via_resend(
            to=to,
            subject=subject,
            html=html,
            from_header=from_header,
            reply_to=reply_to_final,
            tags=tags,
            attachments=attachments,
        )
        await _email_send_record(
            idempotency_key=idempotency_key, to=to, template=template,
            application_id=application_id, candidate_id=candidate_id, result=result,
        )
        return result

    if provider == "smtp" or (provider == "auto" and _settings.smtp_host):
        result = await _send_via_smtp(
            to=to,
            subject=subject,
            html=html,
            from_header=from_header,
            reply_to=reply_to_final,
            attachments=attachments,
        )
        await _email_send_record(
            idempotency_key=idempotency_key, to=to, template=template,
            application_id=application_id, candidate_id=candidate_id, result=result,
        )
        return result

    logger.warning(
        "No email provider configured (Graph creds, Resend key or SMTP host required); skipping send to %s",
        to,
    )
    return EmailSendResult(
        success=False, message_id=None, status_code=0, error="no_provider", provider="none"
    )


async def _email_send_lookup(idempotency_key: str) -> dict[str, Any] | None:
    """Return prior send row as plain dict, or None. Best-effort: failure is non-fatal."""
    try:
        from sqlalchemy import select
        from src.db.base import EmailSend
        from src.db.connection import session_scope
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(EmailSend).where(EmailSend.idempotency_key == idempotency_key)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "status": row.status,
                "provider": row.provider,
                "provider_message_id": row.provider_message_id,
                "error": row.error,
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("idempotency lookup failed (non-fatal): %s", exc)
        return None


async def _email_send_record(
    *,
    idempotency_key: str | None,
    to: str,
    template: str,
    application_id: str | None,
    candidate_id: str | None,
    result: EmailSendResult,
) -> None:
    """Best-effort persistence of a send attempt. Never raises."""
    if not idempotency_key:
        return
    try:
        from uuid import UUID as _UUID
        from src.db.base import EmailSend
        from src.db.connection import session_scope
        async with session_scope() as session:
            row = EmailSend(
                idempotency_key=idempotency_key,
                to_email=to,
                template=template,
                application_id=_UUID(application_id) if application_id else None,
                candidate_id=_UUID(candidate_id) if candidate_id else None,
                provider=result.provider or "unknown",
                provider_message_id=result.message_id,
                status="sent" if result.success else "failed",
                error=(result.error or None) if not result.success else None,
            )
            session.add(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("email_send record write failed (non-fatal): %s", exc)
