"""Teams Adaptive Card via Incoming Webhook.

Zero-friction onboarding for the Grabon team:
  1. In Teams, open the target channel → ⋯ → "Workflows" → "Post to a
     channel when a webhook request is received" (formerly "Incoming
     Webhook" — Microsoft renamed it in 2024 / both still work).
  2. Copy the generated URL.
  3. Set `TEAMS_WEBHOOK_URL` in `.env`. Done.

No app registration, no Azure AD, no graph API. We post the official
MessageCard format which Workflow + Incoming Webhook both accept; we
also include an `attachments[]` Adaptive Card 1.5 body so when the team
graduates to a full Teams app the same payload renders unchanged.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..config import get_settings
from ..logging import get_logger
from .base import Notification, Notifier

log = get_logger(__name__)


class TeamsNotifier(Notifier):
    name = "teams"

    @property
    def available(self) -> bool:
        return bool(get_settings().teams_webhook_url.get_secret_value())

    async def send(self, n: Notification) -> bool:
        url = get_settings().teams_webhook_url.get_secret_value()
        if not url:
            return False
        payload = _build_payload(n)
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json=payload)
        if r.status_code >= 400:
            log.warning("teams.webhook_error", status=r.status_code, body=r.text[:300])
            return False
        return True


_SEVERITY_COLOR = {"info": "0072C6", "warn": "F7B500", "error": "C72A2A"}


def _build_payload(n: Notification) -> dict[str, Any]:
    """Dual-format payload: MessageCard (legacy connector) + Adaptive Card 1.5."""
    facts = [{"name": k, "value": v} for k, v in n.facts]
    actions = [
        {"@type": "OpenUri", "name": label, "targets": [{"os": "default", "uri": url}]}
        for label, url in n.actions
    ]

    card = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": _SEVERITY_COLOR.get(n.severity, "0072C6"),
        "summary": n.summary[:140] or n.title,
        "title": n.title,
        "text": n.summary,
        "sections": [{"facts": facts}] if facts else [],
        "potentialAction": actions,
    }

    adaptive_body = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "text": n.title, "wrap": True},
        {"type": "TextBlock", "text": n.summary, "wrap": True, "spacing": "Small"},
    ]
    if facts:
        adaptive_body.append(
            {
                "type": "FactSet",
                "facts": [{"title": k, "value": v} for k, v in n.facts],
            }
        )
    adaptive_actions = [{"type": "Action.OpenUrl", "title": label, "url": url} for label, url in n.actions]

    card["attachments"] = [
        {
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.5",
                "body": adaptive_body,
                "actions": adaptive_actions,
            },
        }
    ]
    return card
