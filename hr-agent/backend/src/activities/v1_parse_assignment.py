"""V1 activity: parse candidate's assignment submission.

Given uploaded files (R2 keys) + pasted links + notes, extract text from each
file, then call ASSIGNMENT_PARSE_V1 for structured review. Does NOT grade --
HR decides. Writes result into applications.assignment_submission.parse_result.

Enrichment: before the LLM call, fetches real data from GitHub (README, file
tree, languages, commits), Loom (metadata, transcript if API key set), and
checks if the deployed URL is live. All best-effort.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.db.base import Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import save_assignment_submission
from src.llm.client import get_llm_client
from src.llm.model_registry import Stage, model_for
from src.llm.prompt_manager import compile_prompt
from src.llm.prompts import ASSIGNMENT_PARSE_V1, ASSIGNMENT_PARSE_VERSION
from src.models.v1 import (
    AssignmentFileArtifact,
    AssignmentParseResult,
    AssignmentSubmission,
)
from src.config import get_settings
from src.services.file_storage import download
from src.services.resume_extraction import extract_resume_text
from src.services.submission_enrichment import (
    check_deployed_url,
    enrich_github,
    enrich_loom,
)

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


async def _enrich_submission(submission: AssignmentSubmission) -> dict[str, Any]:
    """Run GitHub, Loom, and deployed URL enrichment in parallel. Best-effort."""
    github_url = ""
    loom_url = ""
    for link in submission.links:
        low = link.lower()
        if "github" in low:
            github_url = link.split(": ", 1)[-1].strip() if ": " in link else link.strip()
        elif "loom" in low:
            loom_url = link.split(": ", 1)[-1].strip() if ": " in link else link.strip()

    tasks = []
    keys = []

    if github_url:
        tasks.append(enrich_github(github_url))
        keys.append("github")
    if loom_url:
        tasks.append(enrich_loom(loom_url))
        keys.append("loom")
    if submission.deployed_url:
        tasks.append(check_deployed_url(submission.deployed_url))
        keys.append("deploy_check")

    if not tasks:
        return {}

    results = await asyncio.gather(*tasks, return_exceptions=True)
    enriched: dict[str, Any] = {}
    for key, res in zip(keys, results):
        if isinstance(res, Exception):
            enriched[key] = {"error": str(res)[:200]}
        else:
            enriched[key] = res
    return enriched


def _format_enrichment_for_prompt(enriched: dict[str, Any]) -> str:
    """Format enrichment data as a readable block for the LLM prompt."""
    sections: list[str] = []

    gh = enriched.get("github")
    if gh and not gh.get("error"):
        lines = [
            "=== GITHUB REPO ANALYSIS (fetched live) ===",
            f"Repo: {gh.get('repo_name', 'unknown')}",
            f"Description: {gh.get('description') or '(none)'}",
            f"Languages: {json.dumps(gh.get('languages', {}), ensure_ascii=False)}",
            f"Stars: {gh.get('stars', 0)}, Forks: {gh.get('forks', 0)}, Size: {gh.get('size_kb', 0)} KB",
            f"Created: {gh.get('created_at', '?')}, Last pushed: {gh.get('last_pushed', '?')}",
            f"Is fork: {gh.get('is_fork', False)}",
        ]
        tree = gh.get("file_tree")
        if tree:
            file_names = [f"{f['name']}{'/' if f['type'] == 'dir' else ''}" for f in tree]
            lines.append(f"Top-level files: {', '.join(file_names)}")
        commits = gh.get("recent_commits")
        if commits:
            lines.append(f"Recent commits ({len(commits)}):")
            for c in commits[:5]:
                lines.append(f"  {c.get('sha', '?')} {c.get('date', '?')} - {c.get('message', '?')}")
        readme = gh.get("readme_text")
        if readme:
            lines.append(f"README (first 3000 chars):\n{readme}")
        sections.append("\n".join(lines))
    elif gh and gh.get("error"):
        sections.append(f"=== GITHUB: {gh['error']} ===")

    loom = enriched.get("loom")
    if loom and not loom.get("error"):
        lines = [
            "=== LOOM VIDEO ANALYSIS (fetched live) ===",
            f"Title: {loom.get('title', '(unknown)')}",
            f"Duration: {loom.get('duration_seconds', '?')} seconds",
        ]
        transcript = loom.get("transcript_text")
        if transcript:
            lines.append(f"Transcript (first 5000 chars):\n{transcript}")
        else:
            lines.append("Transcript: not available (no Loom API key or video has no transcript)")
        sections.append("\n".join(lines))
    elif loom and loom.get("error"):
        sections.append(f"=== LOOM: {loom.get('error', loom.get('oembed_error', 'fetch failed'))} ===")

    deploy = enriched.get("deploy_check")
    if deploy and not deploy.get("error"):
        status = "LIVE" if deploy.get("is_live") else "DOWN"
        sections.append(
            f"=== DEPLOYED URL CHECK ===\n"
            f"URL: {deploy.get('url')}\n"
            f"Status: {status} (HTTP {deploy.get('status_code', '?')}, {deploy.get('response_time_ms', '?')}ms)"
        )
    elif deploy and deploy.get("error"):
        sections.append(f"=== DEPLOYED URL: {deploy['error']} ===")

    return "\n\n".join(sections) if sections else ""


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

    enriched = await _enrich_submission(submission)
    enrichment_block = _format_enrichment_for_prompt(enriched)

    prompt = compile_prompt(
        "assignment_parse",
        fallback=ASSIGNMENT_PARSE_V1,
        role_title=role.title,
        assignment_brief=(role.assignment_brief or "")[:3000],
        assignment_instructions=(role.assignment_instructions or "")[:2000],
        project_choice=submission.project_choice or "(not provided)",
        deployed_url=submission.deployed_url or "(not provided)",
        links_json=json.dumps(submission.links, ensure_ascii=False),
        file_texts_json=json.dumps(file_texts, ensure_ascii=False)[:10000],
        candidate_notes=(submission.notes or "")[:2000],
        **scoring_prompt_vars(role.evaluation_spec, role.company_context),
    )

    if enrichment_block:
        prompt += (
            "\n\n--- LIVE ENRICHMENT DATA (fetched from GitHub/Loom/deployed URL) ---\n"
            + enrichment_block
        )

    system_msg = (
        "You review candidate assignment submissions for HR using REAL data "
        "fetched from their GitHub repo, Loom video, and deployed URL. "
        "Analyze the README, file structure, commit history, languages, and "
        "video transcript (when available) to assess quality. Output JSON only."
    )

    client = get_llm_client()
    async with session_scope() as session:
        result = await client.complete(
            prompt=prompt,
            response_model=AssignmentParseResult,
            model=model_for(Stage.ASSIGNMENT_PARSE),
            trace_name="assignment_parse",
            prompt_version=ASSIGNMENT_PARSE_VERSION,
            candidate_id=candidate_id,
            application_id=application_id,
            system=system_msg,
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
                "enriched_sources": list(enriched.keys()),
                "github_error": enriched.get("github", {}).get("error"),
                "loom_error": enriched.get("loom", {}).get("error"),
            },
            model_version=result.model,
            prompt_version=ASSIGNMENT_PARSE_VERSION,
            langfuse_trace_id=result.trace_id,
        )
        return parse_result
