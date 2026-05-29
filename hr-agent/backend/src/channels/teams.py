"""Microsoft Teams notifications.

Used for HR alerts (low-confidence classifications, shortlists, anomalies)
and engineering alerts (LLM budget breaches, retry storms, exceptions).

Set up a webhook once per channel:
  Teams channel → ••• → Workflows → "Post to a channel when a webhook
  request is received" → copy the generated URL into
  TEAMS_WEBHOOK_HR or TEAMS_WEBHOOK_ALERTS.

The payload is an Adaptive Card (v1.4) wrapped in a Workflow-compatible
`attachments` envelope. Works with both the new Workflow connectors and
the legacy Office 365 connectors (which are being retired).

Gracefully no-ops when the webhook URL is absent so dev environments
don't need Teams configured.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()


async def _post(webhook_url: str, payload: dict[str, Any]) -> bool:
    last_err: str = ""
    for attempt in range(3):
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(webhook_url, json=payload)
            except httpx.HTTPError as e:
                last_err = str(e)
                logger.warning("Teams webhook attempt %d failed: %s", attempt + 1, e)
                if attempt < 2:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                continue
        if resp.status_code < 300:
            return True
        last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        logger.warning("Teams attempt %d returned %s", attempt + 1, last_err)
        if resp.status_code < 500:
            break
        if attempt < 2:
            import asyncio
            await asyncio.sleep(2 ** attempt)
    logger.error("Teams webhook failed after retries: %s", last_err)
    return False


def _build_card(title: str, text: str, fields: dict[str, str] | None) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": text,
            "wrap": True,
        },
    ]
    if fields:
        body.append(
            {
                "type": "FactSet",
                "facts": [{"title": k, "value": v} for k, v in fields.items()],
            }
        )
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                },
            }
        ],
    }


async def notify_hr(
    *, title: str, text: str, fields: dict[str, str] | None = None
) -> bool:
    if not _settings.teams_webhook_hr:
        logger.info("TEAMS_WEBHOOK_HR not set; skipping HR notify: %s", title)
        return False
    return await _post(_settings.teams_webhook_hr, _build_card(title, text, fields))


async def notify_alerts(
    *, title: str, text: str, fields: dict[str, str] | None = None
) -> bool:
    if not _settings.teams_webhook_alerts:
        logger.info("TEAMS_WEBHOOK_ALERTS not set; skipping alerts notify: %s", title)
        return False
    return await _post(_settings.teams_webhook_alerts, _build_card(title, text, fields))
