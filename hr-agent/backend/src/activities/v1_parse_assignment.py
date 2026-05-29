"""V1 activity: parse candidate's assignment submission.

Given uploaded files (R2 keys) + pasted links + notes, extract text from each
file, then call ASSIGNMENT_PARSE_V1 for structured review. Does NOT grade --
HR decides. Writes result into applications.assignment_submission.parse_result.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from src.db.base import Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import save_assignment_submission
from src.llm.client import get_llm_client
from src.llm.prompts import ASSIGNMENT_PARSE_V1, ASSIGNMENT_PARSE_VERSION
from src.models.v1 import (
    AssignmentFileArtifact,
    AssignmentParseResult,
    AssignmentSubmission,
)
from src.config import get_settings
from src.services.file_storage import download
from src.services.resume_extraction import extract_resume_text

_settings = get_settings()

logger = logging.getLogger(__name__)


async def _extract_file_text(file: AssignmentFileArtifact) -> str:
    """Best-effort extraction. Returns empty string on any failure."""
    try:
        content = await download(_settings.r2_bucket_resumes, file.r2_key)
        if not content:
            return ""
        result = await extract_resume_text(content=content, filename=file.filename)
        return (result.text or "")[:8000]
    except Exception as e:  # noqa: BLE001
        logger.warning("assignment file extract failed %s: %s", file.filename, e)
        return ""


async def parse_assignment(
    *,
    application_id: UUID,
    candidate_id: UUID,
    role_id: UUID,
    submission: AssignmentSubmission,
) -> AssignmentParseResult:
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise ValueError(f"role {role_id} not found")

        file_texts: list[dict] = []
        for f in submission.files:
            preview = f.extracted_text_preview or await _extract_file_text(f)
            file_texts.append({"filename": f.filename, "text": preview[:4000]})

        prompt = ASSIGNMENT_PARSE_V1.format(
            role_title=role.title,
            assignment_brief=(role.assignment_brief or "")[:3000],
            assignment_instructions=(role.assignment_instructions or "")[:2000],
            project_choice=submission.project_choice or "(not provided)",
            deployed_url=submission.deployed_url or "(not provided)",
            links_json=json.dumps(submission.links, ensure_ascii=False),
            file_texts_json=json.dumps(file_texts, ensure_ascii=False)[:10000],
            candidate_notes=(submission.notes or "")[:2000],
        )

        client = get_llm_client()
        result = await client.complete(
            prompt=prompt,
            response_model=AssignmentParseResult,
            trace_name="assignment_parse",
            prompt_version=ASSIGNMENT_PARSE_VERSION,
            candidate_id=candidate_id,
            application_id=application_id,
            system="You summarize candidate assignment submissions for HR. Output JSON only.",
            max_tokens=2500,
        )

        parse_result = result.parsed
        submission.parse_result = parse_result.model_dump(mode="json")
        submission.submitted_at = submission.submitted_at or datetime.now(UTC)

        await save_assignment_submission(
            session, application_id, submission.model_dump(mode="json")
        )
        await log_audit(
            session,
            candidate_id=candidate_id,
            application_id=application_id,
            action="assignment_parsed",
            actor="agent",
            details={
                "file_count": len(submission.files),
                "link_count": len(submission.links),
                "project_choice": submission.project_choice,
                "deployed_url": submission.deployed_url,
                "trace_id": result.trace_id,
            },
            model_version=result.model,
            prompt_version=ASSIGNMENT_PARSE_VERSION,
            langfuse_trace_id=result.trace_id,
        )
        return parse_result
