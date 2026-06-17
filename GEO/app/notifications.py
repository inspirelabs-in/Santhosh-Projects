import logging
import httpx
from app.config import get_settings
from app.database import run_db
from app.events import broadcast

log = logging.getLogger("geo.notifications")

SEVERITY_LEVELS = ("info", "warning", "critical")


def _build_adaptive_card(title: str, message: str, severity: str = "info") -> dict:
    color = {"critical": "attention", "warning": "warning", "info": "good"}.get(severity, "default")
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "type": "AdaptiveCard",
                "version": "1.4",
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": title,
                        "weight": "Bolder",
                        "size": "Medium",
                        "color": color,
                    },
                    {
                        "type": "TextBlock",
                        "text": message,
                        "wrap": True,
                    },
                    {
                        "type": "TextBlock",
                        "text": "GEO Agent - GrabOn",
                        "size": "Small",
                        "isSubtle": True,
                    },
                ],
            },
        }],
    }


async def _send_teams(title: str, message: str, severity: str):
    settings = get_settings()
    if not settings.teams_webhook_url:
        return
    payload = _build_adaptive_card(title, message, severity)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(settings.teams_webhook_url, json=payload)
            if resp.status_code >= 400:
                log.warning(f"Teams webhook returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"Teams alert failed: {e}")


async def _store_notification(ntype: str, title: str, message: str, severity: str):
    def _insert(conn):
        conn.execute(
            """INSERT INTO notifications (type, title, message, severity)
               VALUES (%s, %s, %s, %s)""",
            (ntype, title, message, severity),
        )
        conn.commit()
    try:
        await run_db(_insert)
    except Exception as e:
        log.error(f"Failed to store notification: {e}")


async def send_alert(
    title: str,
    message: str,
    severity: str = "info",
    ntype: str = "system",
    teams: bool = True,
):
    if severity not in SEVERITY_LEVELS:
        severity = "info"

    await _store_notification(ntype, title, message, severity)

    broadcast("notification", title=title, message=message, severity=severity, ntype=ntype)

    if teams and severity in ("warning", "critical"):
        await _send_teams(title, message, severity)
