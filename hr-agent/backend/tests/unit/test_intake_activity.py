"""Unit test for the intake activity: dedup + consent + ack + audit logging.

Uses an in-memory SQLite database via aiosqlite so no real Postgres is
needed. Outbound Resend + R2 calls are monkey-patched to inspect arguments.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.base import Base
from src.models.candidate import IntakePayload, SourceChannel


@pytest.fixture
async def engine():
    # SQLite URL -- only for the in-process test (pgvector columns will be
    # skipped by JSON fallback; we don't touch candidate_profiles here).
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        # Filter out tables that rely on Postgres-only types.
        metadata = Base.metadata
        keep = {
            "candidates",
            "roles",
            "applications",
            "audit_log",
            "consent_artifacts",
        }
        subset = [t for t in metadata.sorted_tables if t.name in keep]
        for table in subset:
            await conn.run_sync(table.create, checkfirst=True)
    yield eng
    await eng.dispose()


@pytest.fixture
async def patched_deps(engine, monkeypatch):
    """Patch session_scope, Resend send, and R2 upload."""
    from src.activities import intake as intake_mod
    from src.db.repositories import candidate as cand_repo_mod

    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        async with SessionLocal() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(intake_mod, "session_scope", _scope)
    monkeypatch.setattr(cand_repo_mod, "normalise_phone", lambda v: v)
    monkeypatch.setattr(cand_repo_mod, "normalise_linkedin", lambda v: v)

    send_mock = AsyncMock(
        return_value=type(
            "R",
            (),
            {"success": True, "message_id": "re_test_1", "status_code": 200, "error": None},
        )()
    )
    monkeypatch.setattr(intake_mod, "send_email", send_mock)

    upload_mock = AsyncMock(
        return_value=type("S", (), {"key": "candidate/uuid_resume.pdf", "bucket": "test", "size_bytes": 3, "content_type": "application/pdf"})()
    )
    monkeypatch.setattr(intake_mod, "upload_resume", upload_mock)

    return {"send": send_mock, "upload": upload_mock}


@pytest.mark.asyncio
async def test_intake_creates_candidate_and_sends_ack(patched_deps):
    from src.activities.intake import run_intake

    payload = IntakePayload(
        source_channel=SourceChannel.FORM,
        sender_email="alice@example.com",
        sender_name="Alice Kumar",
        sender_phone="+919876543210",
        role_id_hint=None,
        consent_text_shown="I agree",
        consent_ip_address="127.0.0.1",
        raw_payload={
            "role_title": "Senior SDE",
            "_files": [
                {
                    "filename": "resume.pdf",
                    "content_b64": base64.b64encode(b"PDF").decode(),
                    "content_type": "application/pdf",
                }
            ],
        },
    )

    result = await run_intake(payload)

    assert result.acknowledgement_sent is True
    assert result.consent_captured is True
    assert result.was_duplicate is False
    assert len(result.files_stored) == 1
    patched_deps["send"].assert_awaited_once()
    patched_deps["upload"].assert_awaited_once()


@pytest.mark.asyncio
async def test_intake_deduplicates_on_email(patched_deps):
    from src.activities.intake import run_intake

    payload = IntakePayload(
        source_channel=SourceChannel.FORM,
        sender_email="bob@example.com",
        sender_name="Bob",
        raw_payload={"role_title": "Analyst"},
    )
    first = await run_intake(payload)
    second = await run_intake(payload)

    assert first.candidate_id == second.candidate_id
    assert second.was_duplicate is True
    # Ack only sent once (first call); dup path skips it.
    assert patched_deps["send"].await_count == 1
