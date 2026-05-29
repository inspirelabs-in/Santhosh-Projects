"""SMS via MSG91 (DLT-compliant transactional).

India requires all promotional/transactional SMS to be sent via a DLT-
registered template. We reference the registered template ID and pass the
dynamic variables as a comma-separated string (MSG91 "flow" API v5).
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

_MSG91_FLOW_URL = "https://control.msg91.com/api/v5/flow"


@dataclass
class SMSResult:
    success: bool
    message_id: str | None
    status_code: int
    error: str | None = None


async def send_sms(
    *,
    to_phone: str,
    template_id: str | None = None,
    variables: dict[str, str] | None = None,
) -> SMSResult:
    if not _settings.enable_sms_reminders:
        return SMSResult(success=False, message_id=None, status_code=0, error="disabled_by_flag")
    if not _settings.msg91_auth_key:
        logger.info("MSG91 auth key absent; skipping SMS to %s", to_phone)
        return SMSResult(success=False, message_id=None, status_code=0, error="no_credentials")

    normalised = normalise_phone(to_phone)
    if not normalised or not is_valid_indian_mobile(to_phone):
        return SMSResult(success=False, message_id=None, status_code=0, error="invalid_phone")

    tmpl = template_id or _settings.msg91_template_id_reminder
    if not tmpl:
        return SMSResult(success=False, message_id=None, status_code=0, error="no_template_id")

    payload: dict[str, Any] = {
        "template_id": tmpl,
        "sender": _settings.msg91_sender_id,
        "recipients": [
            {
                "mobiles": normalised.lstrip("+"),
                **(variables or {}),
            }
        ],
    }
    headers = {"authkey": _settings.msg91_auth_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(_MSG91_FLOW_URL, json=payload, headers=headers)

    if resp.status_code >= 400:
        logger.error("MSG91 send failed (%s): %s", resp.status_code, resp.text[:500])
        return SMSResult(
            success=False,
            message_id=None,
            status_code=resp.status_code,
            error=resp.text[:500],
        )

    data = resp.json()
    req_id = data.get("request_id") or data.get("type")
    return SMSResult(success=True, message_id=req_id, status_code=resp.status_code)
