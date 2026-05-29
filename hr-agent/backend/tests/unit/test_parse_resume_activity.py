"""Unit tests for the parse_resume activity.

Mocks: R2 download, the text extractor, and LLMClient.complete. We verify
that the parsed profile is persisted, the candidate row is backfilled with
email/phone/linkedin, OCR downgrades confidence, and the audit log records
the Langfuse trace + prompt version.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.base import AuditLog, Base, Candidate
from src.llm.client import LLMResult
from src.models.candidate import CandidateProfile, SourceChannel
from tests.fixtures.sample_resume_text import (
    SAMPLE_PARSED_PROFILE_JSON,
    SAMPLE_RESUME_TEXT,
)


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        keep = {"candidates", "applications", "audit_log"}
        for table in Base.metadata.sorted_tables:
            if table.name in keep:
                await conn.run_sync(table.create, checkfirst=True)
    yield eng
    await eng.dispose()


@pytest.fixture
async def patched(engine, monkeypatch):
    """Patch session_scope, R2 download, text extractor, LLM client.

    We skip persisting CandidateProfileRow because it uses pgvector, which
    SQLite can't create. That path is exercised in integration tests.
    """
    from src.activities import parse_resume as pr
    from src.db.repositories import candidate as cand_repo

    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _scope():
        async with SessionLocal() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(pr, "session_scope", _scope)
    monkeypatch.setattr(pr, "download", AsyncMock(return_value=b"%PDF-FAKE"))
    monkeypatch.setattr(cand_repo, "normalise_phone", lambda v: v)
    monkeypatch.setattr(cand_repo, "normalise_linkedin", lambda v: v)

    # Skip pgvector insert -- assert on the candidate backfill + audit only.
    async def _noop_upsert(session, *, candidate_id, profile, raw_resume_r2_key, embedding=None):
        class _R:
            id = uuid4()

        return _R()

    monkeypatch.setattr(pr, "upsert_candidate_profile", _noop_upsert)

    class _Extr:
        def __init__(self, method: str):
            self.text = SAMPLE_RESUME_TEXT
            self.method = method
            self.page_count = 1
            self.char_count = len(SAMPLE_RESUME_TEXT)

    monkeypatch.setattr(
        pr, "extract_resume_text", AsyncMock(return_value=_Extr("native_pdf"))
    )

    parsed = CandidateProfile.model_validate(SAMPLE_PARSED_PROFILE_JSON)
    llm_result = LLMResult(
        parsed=parsed,
        raw_text="{}",
        model="claude-haiku-4-5",
        prompt_version="v1",
        input_tokens=500,
        output_tokens=300,
        latency_ms=1200,
        trace_id="tr_abc",
        generation_id="gen_abc",
    )

    client_mock = AsyncMock()
    client_mock.complete = AsyncMock(return_value=llm_result)
    monkeypatch.setattr(pr, "get_llm_client", lambda: client_mock)

    return {
        "SessionLocal": SessionLocal,
        "llm": client_mock,
        "extract": pr.extract_resume_text,
    }


@pytest.fixture
async def seeded_candidate(engine):
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with SessionLocal() as s:
        c = Candidate(
            id=uuid4(),
            email=None,
            name="Alice",
            phone=None,
            linkedin_url=None,
            source_channel=SourceChannel.FORM.value,
            status="intake",
        )
        s.add(c)
        await s.commit()
        return c.id


@pytest.mark.asyncio
async def test_parse_resume_backfills_contact_and_audits(patched, seeded_candidate):
    from src.activities.parse_resume import ParseResumeInput, run_parse_resume

    result = await run_parse_resume(
        ParseResumeInput(
            candidate_id=seeded_candidate,
            application_id=None,
            r2_key=f"{seeded_candidate}/uuid_resume.pdf",
            filename="resume.pdf",
        )
    )

    assert result.extraction_method == "native_pdf"
    assert result.trace_id == "tr_abc"
    assert result.needs_hr_review is False

    async with patched["SessionLocal"]() as s:
        cand = await s.get(Candidate, seeded_candidate)
        assert cand.email == "alice@example.com"
        assert cand.phone == "+919876543210"
        assert cand.linkedin_url == "https://www.linkedin.com/in/alice-kumar"
        assert cand.status == "parsed"

        audits = (await s.scalars(select(AuditLog).order_by(AuditLog.id))).all()
        actions = [a.action for a in audits]
        assert "resume_parsed" in actions
        parsed_entry = next(a for a in audits if a.action == "resume_parsed")
        assert parsed_entry.langfuse_trace_id == "tr_abc"
        assert parsed_entry.prompt_version == "v1"


@pytest.mark.asyncio
async def test_ocr_downgrades_confidence(patched, seeded_candidate, monkeypatch):
    from src.activities import parse_resume as pr
    from src.activities.parse_resume import ParseResumeInput, run_parse_resume

    class _Extr:
        text = SAMPLE_RESUME_TEXT
        method = "ocr_pdf"
        page_count = 2
        char_count = len(SAMPLE_RESUME_TEXT)

    monkeypatch.setattr(pr, "extract_resume_text", AsyncMock(return_value=_Extr()))

    captured_profiles: list[CandidateProfile] = []

    async def _capture(session, *, candidate_id, profile, raw_resume_r2_key, embedding=None):
        captured_profiles.append(profile)

        class _R:
            id = uuid4()

        return _R()

    monkeypatch.setattr(pr, "upsert_candidate_profile", _capture)

    await run_parse_resume(
        ParseResumeInput(
            candidate_id=seeded_candidate,
            application_id=None,
            r2_key=f"{seeded_candidate}/ocr.pdf",
            filename="resume.pdf",
        )
    )

    assert captured_profiles, "profile should have been persisted"
    fc = captured_profiles[0].field_confidence
    # Sample fixture has name=0.98, ocr penalty 0.25 → ~0.73.
    assert 0.70 <= fc.name <= 0.76
    assert fc.email < 0.99  # penalty applied
