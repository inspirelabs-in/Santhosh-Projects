"""Teams Adaptive Card payload shape."""
from __future__ import annotations

from grabon_intel.notify.base import Notification
from grabon_intel.notify.teams import _build_payload


def test_payload_contains_both_card_formats() -> None:
    n = Notification(
        title="Hot lead: Mamaearth",
        summary="Score 87 (warm→hot). 3 new signals in last 24h.",
        facts=[("score", "87"), ("tier", "hot"), ("signals_24h", "3")],
        actions=[("Open brand", "http://localhost:3000/brands/42")],
        severity="info",
    )
    p = _build_payload(n)
    assert p["@type"] == "MessageCard"
    assert p["title"] == "Hot lead: Mamaearth"
    assert p["sections"][0]["facts"][0]["name"] == "score"
    assert p["potentialAction"][0]["targets"][0]["uri"] == "http://localhost:3000/brands/42"
    # Adaptive card embedded for forward compat.
    attachments = p["attachments"]
    assert attachments[0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    ad = attachments[0]["content"]
    assert ad["type"] == "AdaptiveCard"
    assert any(b["type"] == "FactSet" for b in ad["body"])


def test_severity_color() -> None:
    p_info = _build_payload(Notification(title="t", summary="s"))
    p_warn = _build_payload(Notification(title="t", summary="s", severity="warn"))
    p_err = _build_payload(Notification(title="t", summary="s", severity="error"))
    assert p_info["themeColor"] != p_warn["themeColor"] != p_err["themeColor"]


def test_notifier_unavailable_without_webhook(monkeypatch) -> None:
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "")
    from grabon_intel.config import get_settings
    from grabon_intel.notify.teams import TeamsNotifier

    get_settings.cache_clear()  # type: ignore[attr-defined]
    assert TeamsNotifier().available is False
