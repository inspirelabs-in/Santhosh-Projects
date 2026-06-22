"""Candidate + application repository.

Thin async helpers that wrap SQLAlchemy. Activities call these; activities
don't build queries inline.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Application, Candidate
from src.models.candidate import ApplicationStatus, CandidateStatus, SourceChannel
from src.services.dedup import normalise_linkedin, normalise_phone


async def get_candidate(session: AsyncSession, candidate_id: UUID) -> Candidate | None:
    return await session.get(Candidate, candidate_id)


async def upsert_candidate(
    session: AsyncSession,
    *,
    email: str | None,
    name: str | None,
    phone: str | None,
    linkedin_url: str | None,
    source_channel: SourceChannel,
    temporal_workflow_id: str | None = None,
    existing_id: UUID | None = None,
) -> Candidate:
    """Create a new candidate, or return the existing one if matched.

    Dedup decision happens upstream in the intake activity -- this function
    just writes. Pass `existing_id` when the dedup layer has already matched.
    """
    normalised_phone = normalise_phone(phone)
    normalised_li = normalise_linkedin(linkedin_url)
    email_l = email.lower() if email else None

    if existing_id is not None:
        candidate = await session.get(Candidate, existing_id)
        if candidate is None:
            raise ValueError(f"existing_id {existing_id} not found")
        # Fill in missing fields -- never overwrite existing non-null values.
        if candidate.email is None and email_l:
            candidate.email = email_l
        if candidate.name is None and name:
            candidate.name = name
        if candidate.phone is None and normalised_phone:
            candidate.phone = normalised_phone
        if candidate.linkedin_url is None and normalised_li:
            candidate.linkedin_url = normalised_li
        return candidate

    candidate = Candidate(
        email=email_l,
        name=name,
        phone=normalised_phone,
        linkedin_url=normalised_li,
        source_channel=source_channel.value,
        status=CandidateStatus.INTAKE.value,
        temporal_workflow_id=temporal_workflow_id,
    )
    session.add(candidate)
    await session.flush()
    return candidate


async def update_candidate_status(
    session: AsyncSession, candidate_id: UUID, status: CandidateStatus
) -> None:
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        raise ValueError(f"candidate {candidate_id} not found")
    candidate.status = status.value


async def create_application(
    session: AsyncSession,
    *,
    candidate_id: UUID,
    role_id: UUID | None,
    status: ApplicationStatus = ApplicationStatus.ACTIVE,
    org_id: UUID | None = None,
) -> Application:
    # Stamp the tenant anchor so org-scoped reads (the durable inbox /
    # actionable-event feed) see this application's events. During the
    # single-tenant phase we resolve the default org when none is passed.
    if org_id is None:
        from src.db.repositories import organization as _org_repo

        default_org = await _org_repo.get_default(session)
        org_id = default_org.id if default_org is not None else None
    app = Application(
        candidate_id=candidate_id,
        role_id=role_id,
        status=status.value,
        org_id=org_id,
    )
    session.add(app)
    await session.flush()
    return app


async def get_application(session: AsyncSession, application_id: UUID) -> Application | None:
    return await session.get(Application, application_id)


async def update_application_status(
    session: AsyncSession, application_id: UUID, status: ApplicationStatus
) -> None:
    app = await session.get(Application, application_id)
    if app is None:
        raise ValueError(f"application {application_id} not found")
    app.status = status.value


async def list_active_applications_for_candidate(
    session: AsyncSession, candidate_id: UUID
) -> list[Application]:
    result = await session.scalars(
        select(Application).where(
            Application.candidate_id == candidate_id,
            Application.status == ApplicationStatus.ACTIVE.value,
        )
    )
    return list(result)
