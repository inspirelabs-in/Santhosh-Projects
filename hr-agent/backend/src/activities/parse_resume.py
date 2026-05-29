"""Stage 3: Parse & Enrich.

Input: R2 key of the resume file + candidate_id.
Work:
  1. Download bytes from R2.
  2. Extract plain text via PyMuPDF → Tesseract fallback → docx.
  3. Call Claude Haiku with PARSE_RESUME_V1 → validated `CandidateProfile`.
  4. Propagate the candidate's phone / email back onto the `candidates` row
     if they were previously null (common when the form captured only name).
  5. Downgrade extraction confidences when OCR was used (OCR output is noisy).
  6. Persist to `candidate_profiles` (append, keep history).
  7. Audit with Langfuse trace id + prompt version.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from temporalio import activity

from src.config import get_settings
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from sqlalchemy import select, update

from src.db.base import Application, Candidate, CandidateProfileRow, ScreeningResponseRow
from src.db.repositories.candidate import get_candidate
from src.db.repositories.candidate_profile import upsert_candidate_profile
from src.llm.client import get_llm_client
from src.llm.prompts import PARSE_RESUME_V1, PARSE_RESUME_VERSION
from src.models.candidate import CandidateProfile, CandidateStatus
from src.services.dedup import normalise_linkedin, normalise_phone
from src.services.file_storage import download
from src.services.resume_extraction import (
    ExtractionMethod,
    UnsupportedFormatError,
    extract_resume_text,
)

logger = logging.getLogger(__name__)
_settings = get_settings()

# Characters beyond this are truncated before the LLM call. PARSE_RESUME_V1
# is token-bounded and resumes above ~20k chars are almost always a multi-PDF
# accident rather than a single real resume.
_MAX_RESUME_CHARS = 30_000

# How much we discount each field's confidence when the text came from OCR.
_OCR_CONFIDENCE_PENALTY = 0.25


@dataclass
class ParseResumeInput:
    candidate_id: UUID
    application_id: UUID | None
    r2_key: str
    filename: str
    # Fallback identity (used only if the resume parser can't extract them).
    # For email ingestion these are the forwarder's From: header. The resume
    # always wins when it provides a value.
    fallback_email: str | None = None
    fallback_name: str | None = None


@dataclass
class ParseResumeResult:
    candidate_id: UUID
    profile_row_id: UUID
    extraction_method: ExtractionMethod
    char_count: int
    trace_id: str | None
    needs_hr_review: bool  # any low-confidence field


def _apply_ocr_penalty(profile: CandidateProfile) -> CandidateProfile:
    fc = profile.field_confidence
    for attr in fc.model_fields:
        current = getattr(fc, attr)
        setattr(fc, attr, max(0.0, current - _OCR_CONFIDENCE_PENALTY))
    return profile


def _has_low_confidence(profile: CandidateProfile, threshold: float = 0.6) -> bool:
    fc = profile.field_confidence
    # Only gate review on fields that materially affect downstream scoring.
    critical = (fc.name, fc.email, fc.current_ctc_lpa, fc.total_years_experience, fc.skills)
    return any(v < threshold for v in critical)


async def run_parse_resume(payload: ParseResumeInput) -> ParseResumeResult:
    # 1. Download bytes
    content = await download(_settings.r2_bucket_resumes, payload.r2_key)

    # 2. Extract text
    try:
        extraction = await extract_resume_text(content=content, filename=payload.filename)
    except UnsupportedFormatError as e:
        async with session_scope() as session:
            await log_audit(
                session,
                action="resume_parse_failed",
                actor="agent",
                candidate_id=payload.candidate_id,
                application_id=payload.application_id,
                details={"reason": "unsupported_format", "error": str(e), "r2_key": payload.r2_key},
            )
        raise

    text = extraction.text[:_MAX_RESUME_CHARS]
    if not text.strip():
        async with session_scope() as session:
            await log_audit(
                session,
                action="resume_parse_failed",
                actor="agent",
                candidate_id=payload.candidate_id,
                application_id=payload.application_id,
                details={"reason": "empty_text", "method": extraction.method, "r2_key": payload.r2_key},
            )
        raise ValueError("Resume produced no extractable text")

    # 3. LLM structured extraction
    client = get_llm_client()
    prompt = PARSE_RESUME_V1.format(resume_text=text)
    result = await client.complete(
        prompt=prompt,
        response_model=CandidateProfile,
        model=_settings.llm_model_fast,
        trace_name="parse_resume",
        prompt_version=PARSE_RESUME_VERSION,
        candidate_id=payload.candidate_id,
        application_id=payload.application_id,
        temperature=0.0,
        max_tokens=6000,
        metadata={"extraction_method": extraction.method, "char_count": extraction.char_count},
    )
    profile = result.parsed

    # 4. Normalise contact fields the model returned.
    if profile.phone:
        profile.phone = normalise_phone(profile.phone) or profile.phone
    if profile.linkedin_url:
        profile.linkedin_url = normalise_linkedin(profile.linkedin_url) or profile.linkedin_url

    # 5. Downgrade confidence for OCR-derived text.
    if extraction.method == "ocr_pdf":
        profile = _apply_ocr_penalty(profile)

    needs_hr_review = _has_low_confidence(profile)

    # 6. Persist resume-derived identity onto the candidate row. Resume data
    #    is the source of truth; any placeholder written at intake (sender
    #    email, forwarder name) is overwritten here.
    async with session_scope() as session:
        candidate = await get_candidate(session, payload.candidate_id)
        if candidate is None:
            raise ValueError(f"Candidate {payload.candidate_id} not found")

        resume_email = str(profile.email).lower() if profile.email else None
        resume_phone = profile.phone or None
        resume_linkedin = profile.linkedin_url or None
        resume_name = profile.name or None

        # Fallbacks: only used when the resume did not supply the field. Resume
        # always wins. Fallback email is lower-cased; fallback name kept as-is.
        fallback_email = (
            payload.fallback_email.lower() if payload.fallback_email else None
        )
        fallback_name = payload.fallback_name or None
        effective_email = resume_email or fallback_email
        effective_name = resume_name or fallback_name

        # Check whether another candidate row already owns this identity. Can
        # happen when the same applicant applies twice (different mail forwarder
        # or direct careers form). Merge this application into the prior row
        # and drop the placeholder row created at intake.
        merged_into: UUID | None = None
        if effective_email or resume_phone or resume_linkedin:
            filters = []
            if effective_email:
                filters.append(Candidate.email == effective_email)
            if resume_phone:
                filters.append(Candidate.phone == resume_phone)
            if resume_linkedin:
                filters.append(Candidate.linkedin_url == resume_linkedin)
            from sqlalchemy import or_
            other = await session.scalar(
                select(Candidate)
                .where(Candidate.id != candidate.id)
                .where(or_(*filters))
                .limit(1)
            )
            if other is not None:
                merged_into = other.id
                # Postgres advisory lock keyed on the surviving candidate id
                # serialises concurrent merges into the same target row. Without
                # this, two parallel resume_parse jobs racing to merge into
                # ``other`` could double-update applications or violate FKs.
                from sqlalchemy import text as _text
                lock_int = hash(str(other.id)) & ((1 << 63) - 1)
                await session.execute(
                    _text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=lock_int)
                )
                await session.execute(
                    update(Application)
                    .where(Application.candidate_id == candidate.id)
                    .values(candidate_id=other.id)
                )
                # NOTE: audit_log rows are append-only (DB trigger). We keep
                # historical rows pointing at the placeholder candidate id and
                # write a new ``candidate_merged`` row on the surviving id below
                # so the audit trail reads forward-only.
                await session.execute(
                    update(CandidateProfileRow)
                    .where(CandidateProfileRow.candidate_id == candidate.id)
                    .values(candidate_id=other.id)
                )
                # Best-effort fill missing fields on the surviving candidate.
                if other.email is None and effective_email:
                    other.email = effective_email
                if other.phone is None and resume_phone:
                    other.phone = resume_phone
                if other.linkedin_url is None and resume_linkedin:
                    other.linkedin_url = resume_linkedin
                if other.name is None and effective_name:
                    other.name = effective_name
                await session.delete(candidate)
                await session.flush()
                target_candidate_id = other.id
                # Re-bind `candidate` to the surviving row so the profile
                # upsert + audit rows below attach to it.
                placeholder_id = candidate.id
                candidate = other
                await log_audit(
                    session,
                    action="candidate_merged",
                    actor="agent",
                    candidate_id=other.id,
                    application_id=payload.application_id,
                    details={
                        "placeholder_id": str(placeholder_id),
                        "matched_on": {
                            "email": effective_email if effective_email else None,
                            "phone": resume_phone if resume_phone else None,
                            "linkedin": resume_linkedin if resume_linkedin else None,
                        },
                    },
                )
            else:
                target_candidate_id = candidate.id
        else:
            target_candidate_id = candidate.id

        if merged_into is None:
            # Resume wins. Fallbacks (sender email/name) fill only the gaps.
            if effective_email:
                candidate.email = effective_email
            if resume_phone:
                candidate.phone = resume_phone
            if resume_linkedin:
                candidate.linkedin_url = resume_linkedin
            if effective_name:
                candidate.name = effective_name

        candidate.status = CandidateStatus.PARSED.value

        row = await upsert_candidate_profile(
            session,
            candidate_id=target_candidate_id,
            profile=profile,
            raw_resume_r2_key=payload.r2_key,
        )

        await log_audit(
            session,
            action="resume_parsed",
            actor="agent",
            candidate_id=target_candidate_id,
            application_id=payload.application_id,
            details={
                "r2_key": payload.r2_key,
                "extraction_method": extraction.method,
                "char_count": extraction.char_count,
                "needs_hr_review": needs_hr_review,
                "merged_from_placeholder_id": str(payload.candidate_id) if merged_into else None,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
            model_version=result.model,
            prompt_version=result.prompt_version,
            langfuse_trace_id=result.trace_id,
        )

        return ParseResumeResult(
            candidate_id=target_candidate_id,
            profile_row_id=row.id,
            extraction_method=extraction.method,
            char_count=extraction.char_count,
            trace_id=result.trace_id,
            needs_hr_review=needs_hr_review,
        )


# ---------------------------------------------------------------------------
# Temporal wrapper
# ---------------------------------------------------------------------------


@activity.defn(name="parse_resume")
async def parse_resume_activity(payload: ParseResumeInput) -> ParseResumeResult:
    return await run_parse_resume(payload)
