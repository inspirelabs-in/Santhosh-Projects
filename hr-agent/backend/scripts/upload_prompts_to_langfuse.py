#!/usr/bin/env python3
"""Upload local prompt templates to Langfuse Prompt Management.

Captures the version assigned by Langfuse and auto-updates the local
*_VERSION constant in each prompt file so traces stay aligned.

Usage:
    cd backend
    # Set env vars (or use .env):
    #   LANGFUSE_PUBLIC_KEY=pk-lf-...
    #   LANGFUSE_SECRET_KEY=sk-lf-...
    #   LANGFUSE_HOST=https://cloud.langfuse.com   (or your self-hosted URL)
    python scripts/upload_prompts_to_langfuse.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Allow running from backend/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from langfuse import Langfuse

# ---------------------------------------------------------------------------
# Prompt constants (local Python source)
# ---------------------------------------------------------------------------
from src.llm.prompts.assignment_parse import ASSIGNMENT_PARSE_V1
from src.llm.prompts.ceo_brief import CEO_BRIEF_V1
from src.llm.prompts.fit_score import FIT_SCORE_V1
from src.llm.prompts.interview_report import INTERVIEW_REPORT_V1
from src.llm.prompts.journey_report import JOURNEY_REPORT_V1
from src.llm.prompts.meeting_analysis import MEETING_ANALYSIS_V1
from src.llm.prompts.parse_resume import PARSE_RESUME_V1
from src.llm.prompts.rejection_message import REJECTION_MESSAGE_V1
from src.llm.prompts.screening_eval import SCREENING_EVAL_V1
from src.llm.prompts.screening_gen import SCREENING_GEN_V1
from src.llm.prompts.voice_screen_eval_audio import VOICE_SCREEN_EVAL_AUDIO_V1, VOICE_SCREEN_EVAL_AUDIO_VERSION
from src.llm.prompts.voice_screening import VOICE_SCREEN_GEN_V1
from src.llm.prompts.role_drafting import (
    ROLE_DRAFT_SYSTEM,
    SECTION_REWRITE_SYSTEM,
    LINKEDIN_POST_SYSTEM,
)
from src.agent.prompts.assignment import ASSIGNMENT_GEN_V1
from src.llm.prompts.jd_generation import JD_GENERATION_SYSTEM, JD_GENERATION_VERSION

# ---------------------------------------------------------------------------
# Registry: (langfuse_name, prompt_text, version_var_name, file_path)
# ---------------------------------------------------------------------------
_script_dir = Path(__file__).resolve().parent
BACKEND = _script_dir.parent if _script_dir.name == "scripts" else _script_dir

PROMPTS = [
    ("fit_score",             FIT_SCORE_V1,           "FIT_SCORE_VERSION",           BACKEND / "src" / "llm" / "prompts" / "fit_score.py"),
    ("screening_gen",         SCREENING_GEN_V1,       "SCREENING_GEN_VERSION",       BACKEND / "src" / "llm" / "prompts" / "screening_gen.py"),
    ("screening_eval",        SCREENING_EVAL_V1,      "SCREENING_EVAL_VERSION",      BACKEND / "src" / "llm" / "prompts" / "screening_eval.py"),
    ("parse_resume",          PARSE_RESUME_V1,        "PARSE_RESUME_VERSION",        BACKEND / "src" / "llm" / "prompts" / "parse_resume.py"),
    ("assignment_parse",      ASSIGNMENT_PARSE_V1,    "ASSIGNMENT_PARSE_VERSION",    BACKEND / "src" / "llm" / "prompts" / "assignment_parse.py"),
    ("meeting_analysis",      MEETING_ANALYSIS_V1,    "MEETING_ANALYSIS_VERSION",    BACKEND / "src" / "llm" / "prompts" / "meeting_analysis.py"),
    ("journey_report",        JOURNEY_REPORT_V1,      "JOURNEY_REPORT_VERSION",      BACKEND / "src" / "llm" / "prompts" / "journey_report.py"),
    ("interview_report",      INTERVIEW_REPORT_V1,    "INTERVIEW_REPORT_VERSION",    BACKEND / "src" / "llm" / "prompts" / "interview_report.py"),
    ("ceo_brief",             CEO_BRIEF_V1,           "CEO_BRIEF_VERSION",           BACKEND / "src" / "llm" / "prompts" / "ceo_brief.py"),
    ("rejection_message",     REJECTION_MESSAGE_V1,   "REJECTION_MESSAGE_VERSION",   BACKEND / "src" / "llm" / "prompts" / "rejection_message.py"),
    ("voice_screen_gen",      VOICE_SCREEN_GEN_V1,    "VOICE_SCREEN_GEN_VERSION",    BACKEND / "src" / "llm" / "prompts" / "voice_screening.py"),
    ("voice_screen_eval",     VOICE_SCREEN_EVAL_AUDIO_V1, "VOICE_SCREEN_EVAL_AUDIO_VERSION", BACKEND / "src" / "llm" / "prompts" / "voice_screen_eval_audio.py"),
    ("role_draft_system",     ROLE_DRAFT_SYSTEM,      "ROLE_DRAFT_VERSION",          BACKEND / "src" / "llm" / "prompts" / "role_drafting.py"),
    ("section_rewrite_system", SECTION_REWRITE_SYSTEM, "SECTION_REWRITE_VERSION",    BACKEND / "src" / "llm" / "prompts" / "role_drafting.py"),
    ("linkedin_post_system",  LINKEDIN_POST_SYSTEM,   "LINKEDIN_POST_VERSION",       BACKEND / "src" / "llm" / "prompts" / "role_drafting.py"),
    ("assignment_gen",        ASSIGNMENT_GEN_V1,      "ASSIGNMENT_GEN_VERSION",      BACKEND / "src" / "agent" / "prompts" / "assignment.py"),
    ("jd_generation",         JD_GENERATION_SYSTEM,   "JD_GENERATION_VERSION",       BACKEND / "src" / "llm" / "prompts" / "jd_generation.py"),
]


def _update_version_in_file(file_path: Path, var_name: str, new_version: int) -> None:
    """Replace ``FIT_SCORE_VERSION = "v6"`` with ``FIT_SCORE_VERSION = "v<N>"``."""
    pattern = re.compile(rf'^({re.escape(var_name)}\s*=\s*)"[^"]*"\s*$', re.MULTILINE)
    replacement = rf'\1"v{new_version}"  # Langfuse version {new_version}'
    raw = file_path.read_text(encoding="utf-8")
    if not pattern.search(raw):
        print(f"  [SKIP] {var_name} — version line not found in {file_path.name}")
        return
    updated = pattern.sub(replacement, raw)
    if updated != raw:
        file_path.write_text(updated, encoding="utf-8")
        print(f"  [VER]  {var_name} → v{new_version}")


def main() -> None:
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        print("ERROR: Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY env vars.")
        print("Find them at: Langfuse -> Settings -> API Keys")
        sys.exit(1)

    lf = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
    print(f"Connected to Langfuse at {host}\n")

    success = 0
    failed = 0
    version_updates = 0

    for name, text, var_name, file_path in PROMPTS:
        try:
            prompt = lf.create_prompt(
                name=name,
                prompt=text,
                type="text",
                labels=["production"],
            )
            lf_ver = prompt.version
            print(f"  [OK]  {name} → Langfuse v{lf_ver}")
            success += 1

            _update_version_in_file(file_path, var_name, lf_ver)
            version_updates += 1

        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            failed += 1

    lf.flush()
    print(f"\nDone. Uploaded: {success}, Failed: {failed}, Total: {len(PROMPTS)}")
    print(f"Local version constants updated: {version_updates}")
    print("\nNext steps:")
    print("  1. Open Langfuse -> Prompts to see all uploaded prompts")
    print("  2. Edit prompts in browser — changes go live within 60s")
    print("  3. To bump a prompt again, re-run this script or use the Langfuse UI")


if __name__ == "__main__":
    main()
