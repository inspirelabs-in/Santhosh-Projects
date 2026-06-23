"""V1 activity: generate markdown journey report for HR.

Reads all persisted state for the application (profile, screening, assignment,
audit trail), then asks SMART model for a one-page synthesis. Writes to
applications.journey_report.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select

from src.db.base import Application, Candidate, CandidateProfileRow, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import save_journey_report
from src.llm.client import get_llm_client
from src.llm.model_registry import Stage, model_for
from src.llm.prompt_manager import compile_prompt
from src.llm.prompts import JOURNEY_REPORT_V1, JOURNEY_REPORT_VERSION

logger = logging.getLogger(__name__)


class _MarkdownOut(BaseModel):
    markdown: str


_BOLD_RE = __import__("re").compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = __import__("re").compile(r"(?<!\*)\*(?!\s)([^*\n]+?)\*(?!\*)")


def _sanitize_markdown(md: str) -> str:
    """Strip `**bold**` / `*italic*` wrappers so plain renderers don't leak asterisks.

    The journey_report prompt forbids bold/italic, but LLMs still emit it
    sometimes. Keep the inner text, drop the markers.
    """
    if not md:
        return md
    md = _BOLD_RE.sub(r"\1", md)
    md = _ITALIC_RE.sub(r"\1", md)
    # Trim leftover trailing colons in labels like "Current Role: **X**:" edge cases.
    return md


async def generate_journey_report(*, application_id: UUID) -> str:
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise ValueError(f"application {application_id} not found")
        candidate = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None

        profile_row = (
            await session.scalars(
                select(CandidateProfileRow)
                .where(CandidateProfileRow.candidate_id == app.candidate_id)
                .order_by(CandidateProfileRow.created_at.desc())
                .limit(1)
            )
        ).first()
        profile_summary = {}
        if profile_row and profile_row.parsed_data:
            p = profile_row.parsed_data
            profile_summary = {
                "current_role": p.get("current_role"),
                "current_company": p.get("current_company"),
                "total_years_experience": p.get("total_years_experience"),
                "skills_top": (p.get("skills") or [])[:10],
                "current_ctc_lpa": p.get("current_ctc_lpa"),
                "expected_ctc_lpa": p.get("expected_ctc_lpa"),
                "notice_period_days": p.get("notice_period_days"),
                "location": p.get("location"),
            }

        screening_eval = app.screening_evaluation or {}
        screening_verdict = screening_eval.get("verdict", "n/a")
        logistics_values = screening_eval.get("logistics_values") or {}
        assignment = app.assignment_submission or {}
        assignment_summary = assignment.get("parse_result", {}) if assignment else {}

        sq = app.screening_questions or {}
        questions_only = (
            sq.get("questions", []) if isinstance(sq, dict) else []
        )
        submission = sq.get("submission") if isinstance(sq, dict) else None
        answers = (submission or {}).get("answers", []) if submission else []

        # Load company context + evaluation spec for generic prompt
        _cc = (role.company_context if role and hasattr(role, 'company_context') else None) or {}
        _es = (role.evaluation_spec if role and hasattr(role, 'evaluation_spec') else None) or {}
        import json as _json
        _cc_json = _json.dumps(_cc, ensure_ascii=False, default=str)[:2000]
        _es_json = _json.dumps(_es, ensure_ascii=False, default=str)[:2000]

        prompt = compile_prompt(
            "journey_report",
            fallback=JOURNEY_REPORT_V1,
            company_context_json=_cc_json,
            evaluation_spec_json=_es_json,
            role_title=role.title if role else "n/a",
            candidate_name=(candidate.name if candidate else "") or "n/a",
            candidate_email=(candidate.email if candidate else "") or "n/a",
            applied_at=app.created_at.isoformat() if app.created_at else "n/a",
            source_channel=candidate.source_channel if candidate else "n/a",
            profile_summary=json.dumps(profile_summary, ensure_ascii=False)[:2000],
            screening_questions_json=json.dumps(questions_only, ensure_ascii=False)[:3000],
            screening_responses_json=json.dumps(answers, ensure_ascii=False)[:4000],
            screening_evaluation_json=json.dumps(screening_eval, ensure_ascii=False)[:3000],
            logistics_values_json=json.dumps(logistics_values, ensure_ascii=False)[:1000],
            screening_verdict=screening_verdict,
            assignment_sent_at=(app.updated_at.isoformat() if app.updated_at else "n/a"),
            assignment_summary_json=json.dumps(assignment_summary, ensure_ascii=False)[:3000],
            current_stage=app.current_stage,
        )

        client = get_llm_client()
        result = await client.complete(
            prompt=prompt,
            response_model=_MarkdownOut,
            model=model_for(Stage.JOURNEY_REPORT),
            trace_name="journey_report",
            prompt_version=JOURNEY_REPORT_VERSION,
            candidate_id=app.candidate_id,
            application_id=application_id,
            system=(
                'Return strict JSON of shape {"markdown": "..."}. '
                "The markdown value is the full report."
            ),
            max_tokens=1500,
        )

        markdown = _sanitize_markdown(result.parsed.markdown)
        await save_journey_report(session, application_id, markdown)
        await log_audit(
            session,
            candidate_id=app.candidate_id,
            application_id=application_id,
            action="journey_report_generated",
            actor="agent",
            details={
                "trace_id": result.trace_id,
                "length_chars": len(markdown),
                "generated_at": datetime.now(UTC).isoformat(),
            },
            model_version=result.model,
            prompt_version=JOURNEY_REPORT_VERSION,
            langfuse_trace_id=result.trace_id,
        )
        return markdown
