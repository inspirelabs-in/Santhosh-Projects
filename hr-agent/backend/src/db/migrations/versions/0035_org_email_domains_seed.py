"""Seed organizations.settings["email_domains"] for internal-mail detection.

The inbound-mail funnel (services/email_filter) tells *internal* org mail from
*candidate* mail using the org's own email domains, stored as a JSON list on
``organizations.settings["email_domains"]`` (no dedicated table, per design).

This migration backfills that list for orgs that don't have one yet, derived from
the configured careers/from addresses (``RESEND_FROM_EMAIL`` /
``ADMIN_NOTIFY_EMAIL`` etc., default ``careers@grabon.in`` -> ``grabon.in``).
It only fills when the key is absent/empty, so a hand-edited list is preserved.
Additive / non-breaking; data-only.

Revision ID: 0035_org_email_domains_seed
Revises: 0034_voice_processing_status
Create Date: 2026-06-22
"""

from __future__ import annotations

import json
from typing import Sequence

from alembic import op

revision: str = "0035_org_email_domains_seed"
down_revision: str | Sequence[str] | None = "0034_voice_processing_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _configured_domains() -> list[str]:
    """Best-effort: derive org domains from settings; default to grabon.in."""
    domains: list[str] = []
    try:
        from src.config import get_settings

        s = get_settings()
        for addr in (
            getattr(s, "resend_from_email", None),
            getattr(s, "admin_notify_email", None),
            getattr(s, "email_from_hr", None),
            getattr(s, "email_from_ceo", None),
            getattr(s, "email_from_technical", None),
            getattr(s, "graph_organiser_email", None),
        ):
            if addr and "@" in addr:
                dom = addr.rsplit("@", 1)[1].strip().lower()
                if dom and dom not in domains:
                    domains.append(dom)
    except Exception:
        pass
    if not domains:
        domains = ["grabon.in"]
    return domains


def upgrade() -> None:
    domains = _configured_domains()
    payload = json.dumps(domains)
    # Only fill orgs whose settings lack a non-empty email_domains list. Uses
    # jsonb_set + COALESCE so we never clobber an existing hand-curated list.
    op.execute(
        f"""
        UPDATE organizations
           SET settings = jsonb_set(
                   COALESCE(settings, '{{}}'::jsonb),
                   '{{email_domains}}',
                   '{payload}'::jsonb,
                   true
               )
         WHERE COALESCE(jsonb_array_length(settings -> 'email_domains'), 0) = 0
        """
    )


def downgrade() -> None:
    # Remove only the key we added; leave the rest of settings intact.
    op.execute(
        "UPDATE organizations SET settings = settings - 'email_domains'"
    )
