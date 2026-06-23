"""DPDP-compliant consent capture.

Every intake event must produce a `ConsentArtifact` row. The text shown to
the candidate is a versioned constant -- never edit in place, bump the
version so older consents remain faithful to what the candidate saw.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import ConsentArtifactRow
from src.models.audit import ConsentChannel, ConsentType

PRIVACY_NOTICE_VERSION = "v1"

PRIVACY_NOTICE_TEXT_V1 = """GrabOn (GrabOn Solutions Pvt. Ltd.) processes your application for
recruitment purposes under India's Digital Personal Data Protection Act 2023.

Your data (name, contact details, resume, and application context) is used to:
  - Evaluate your candidacy for the role you applied to
  - Communicate with you via email, WhatsApp, or SMS during the hiring process
  - Retain your profile for 6 months after a hiring decision (or 18 months if
    you opt into our talent pool for future roles)

AI assistance helps screen and schedule. Human recruiters review all
decisions before any outcome is shared with you.

You can request deletion of your data at any time by emailing
privacy@grabon.in with your application reference ID. For questions about
how your data is handled, contact our Data Protection Officer at
dpo@grabon.in."""


async def capture_consent(
    session: AsyncSession,
    *,
    candidate_id: UUID,
    consent_type: ConsentType = ConsentType.HIRING,
    consent_text: str = PRIVACY_NOTICE_TEXT_V1,
    channel: ConsentChannel = ConsentChannel.FORM,
    ip_address: str | None = None,
) -> ConsentArtifactRow:
    row = ConsentArtifactRow(
        candidate_id=candidate_id,
        consent_type=consent_type.value,
        consent_text=consent_text,
        channel=channel.value,
        ip_address=ip_address,
    )
    session.add(row)
    await session.flush()
    return row
