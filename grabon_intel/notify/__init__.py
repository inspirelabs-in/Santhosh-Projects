"""Notifier layer. Two channels supported today:

  - Microsoft Teams via incoming webhook (Adaptive Card payload)
  - SMTP email (works for Outlook 365 too via SMTP creds)

Slack is intentionally not built — team uses Teams. Slack adapter slots
in later via the same `Notifier` interface; no caller changes needed.
"""
from .base import Notifier, NotifierError, get_notifiers
from .email import EmailNotifier
from .teams import TeamsNotifier

__all__ = ["Notifier", "NotifierError", "TeamsNotifier", "EmailNotifier", "get_notifiers"]
