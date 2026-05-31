"""SMTP email notifier. Renders a Jinja2 template + a plaintext fallback."""
from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from jinja2 import Template

from ..config import get_settings
from ..logging import get_logger
from .base import Notification, Notifier

log = get_logger(__name__)


_HTML = Template(
    """<!doctype html>
<html><body style="font-family: -apple-system, Segoe UI, Roboto, sans-serif; line-height: 1.45;">
  <h2 style="margin:0 0 8px;">{{ n.title }}</h2>
  <p style="margin:0 0 12px; color:#444;">{{ n.summary }}</p>
  {% if n.facts %}<table cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
    {% for k, v in n.facts %}<tr><td style="border-bottom:1px solid #eee;color:#666;">{{ k }}</td>
      <td style="border-bottom:1px solid #eee;"><b>{{ v }}</b></td></tr>{% endfor %}
  </table>{% endif %}
  {% if n.actions %}<p style="margin-top:14px;">
    {% for label, url in n.actions %}<a href="{{ url }}"
       style="display:inline-block;padding:8px 14px;margin-right:8px;background:#0072C6;color:#fff;border-radius:4px;text-decoration:none;">{{ label }}</a>
    {% endfor %}</p>{% endif %}
</body></html>"""
)


class EmailNotifier(Notifier):
    name = "email"

    @property
    def available(self) -> bool:
        s = get_settings()
        return bool(s.digest_smtp_host and s.digest_email_to and s.digest_from)

    async def send(self, n: Notification) -> bool:
        if not self.available:
            return False
        s = get_settings()

        def _send_sync() -> bool:
            msg = EmailMessage()
            msg["From"] = s.digest_from
            msg["To"] = s.digest_email_to
            msg["Subject"] = n.title[:120]
            facts_plain = "\n".join(f"  {k}: {v}" for k, v in n.facts)
            actions_plain = "\n".join(f"  {label}: {url}" for label, url in n.actions)
            msg.set_content(
                f"{n.title}\n\n{n.summary}\n\n{facts_plain}\n\n{actions_plain}".strip()
            )
            msg.add_alternative(_HTML.render(n=n), subtype="html")
            try:
                with smtplib.SMTP(s.digest_smtp_host, s.digest_smtp_port) as smtp:
                    smtp.starttls()
                    if s.digest_smtp_user:
                        smtp.login(s.digest_smtp_user, s.digest_smtp_pass.get_secret_value())
                    smtp.send_message(msg)
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("email.send_failed", exc=str(exc))
                return False

        return await asyncio.to_thread(_send_sync)
