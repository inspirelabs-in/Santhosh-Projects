"""Outreach — SMTP send + IMAP reply poll (replaces Smartlead)."""
from .imap_poller import poll_replies
from .smtp_sender import SendResult, send_email

__all__ = ["send_email", "SendResult", "poll_replies"]
