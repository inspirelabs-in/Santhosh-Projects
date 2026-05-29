"""WhatsApp Business API via Meta Cloud API (direct).

Only template messages go outbound (Meta requires pre-approved templates for
anything sent to a user outside a 24-hour service window). The templates
themselves are defined in Meta Business Manager; we just reference them by
name and pass the variables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import get_settings
from src.services.dedup import is_valid_indian_mobile, normalise_phone

logger = logging.getLogger(__name__)
_settings = get_settings()

_META_GRAPH = "https://graph.facebook.com/v20.0"


@dataclass
class WhatsAppResult:
    success: bool
    message_id: str | None
    status_code: int
    error: str | None = None


def _to_e164_digits(phone: str) -> str | None:
    """Meta expects digits only (no leading +)."""
    normalised = normalise_phone(phone)
    if not normalised:
        return None
    return normalised.lstrip("+")


async def send_template(
    *,
    to_phone: str,
    template_name: str,
    body_params: list[str] | None = None,
    button_url_param: str | None = None,
    language_code: str = "en_US",
) -> WhatsAppResult:
    """Send a pre-approved template message.

    `body_params` maps to `{{1}}..{{n}}` in the template body.
    `button_url_param` is the dynamic suffix for a URL button (e.g. the JWT
    screening token).
    """
    if not _settings.enable_whatsapp:
        return WhatsAppResult(
            success=False, message_id=None, status_code=0, error="disabled_by_flag"
        )
    if not (_settings.whatsapp_access_token and _settings.whatsapp_phone_number_id):
        logger.info("WhatsApp credentials absent; skipping send to %s", to_phone)
        return WhatsAppResult(
            success=False, message_id=None, status_code=0, error="no_credentials"
        )

    recipient = _to_e164_digits(to_phone)
    if not recipient or not is_valid_indian_mobile(to_phone):
        return WhatsAppResult(
            success=False, message_id=None, status_code=0, error="invalid_phone"
        )

    components: list[dict[str, Any]] = []
    if body_params:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in body_params],
            }
        )
    if button_url_param:
        components.append(
            {
                "type": "button",
                "sub_type": "url",
                "index": "0",
                "parameters": [{"type": "text", "text": button_url_param}],
            }
        )

    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": components,
        },
    }

    url = f"{_META_GRAPH}/{_settings.whatsapp_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {_settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code >= 400:
        logger.error("WhatsApp send failed (%s): %s", resp.status_code, resp.text[:500])
        return WhatsAppResult(
            success=False,
            message_id=None,
            status_code=resp.status_code,
            error=resp.text[:500],
        )

    data = resp.json()
    msg_id = (data.get("messages") or [{}])[0].get("id")
    return WhatsAppResult(success=True, message_id=msg_id, status_code=resp.status_code)
