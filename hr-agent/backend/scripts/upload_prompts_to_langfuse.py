#!/usr/bin/env python3
"""One-time script: upload all local prompt templates to Langfuse Prompt Management.

Run once after creating your Langfuse account. After this, team edits prompts
in the Langfuse UI and the backend fetches them at runtime.

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
import sys

# Allow running from backend/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from langfuse import Langfuse

# ---------------------------------------------------------------------------
# LLM prompts (backend/src/llm/prompts/)
# ---------------------------------------------------------------------------
from src.llm.prompts.assignment_parse import ASSIGNMENT_PARSE_V1
from src.llm.prompts.classify_email import CLASSIFY_EMAIL_V1
from src.llm.prompts.ceo_brief import CEO_BRIEF_V1
from src.llm.prompts.fit_score import FIT_SCORE_V1
from src.llm.prompts.interview_report import INTERVIEW_REPORT_V1
from src.llm.prompts.journey_report import JOURNEY_REPORT_V1
from src.llm.prompts.meeting_analysis import MEETING_ANALYSIS_V1
from src.llm.prompts.parse_resume import PARSE_RESUME_V1
from src.llm.prompts.rejection_message import REJECTION_MESSAGE_V1
from src.llm.prompts.score_open_text import SCORE_OPEN_TEXT_V1
from src.llm.prompts.screening_eval import SCREENING_EVAL_V1
from src.llm.prompts.screening_gen import SCREENING_GEN_V1
from src.llm.prompts.voice_screening import VOICE_SCREEN_GEN_V1, VOICE_SCREEN_EVAL_V1
from src.llm.prompts.role_drafting import (
    ROLE_DRAFT_SYSTEM,
    SECTION_REWRITE_SYSTEM,
    LINKEDIN_POST_SYSTEM,
)

# ---------------------------------------------------------------------------
# Agent prompts (backend/src/agent/prompts/)
# ---------------------------------------------------------------------------
from src.agent.prompts.assignment import ASSIGNMENT_GEN_V1

# ---------------------------------------------------------------------------
# Prompt registry: (langfuse_name, prompt_text)
# ---------------------------------------------------------------------------
PROMPTS = [
    # LLM prompts
    ("fit_score", FIT_SCORE_V1),
    ("screening_gen", SCREENING_GEN_V1),
    ("screening_eval", SCREENING_EVAL_V1),
    ("parse_resume", PARSE_RESUME_V1),
    ("classify_email", CLASSIFY_EMAIL_V1),
    ("assignment_parse", ASSIGNMENT_PARSE_V1),
    ("meeting_analysis", MEETING_ANALYSIS_V1),
    ("journey_report", JOURNEY_REPORT_V1),
    ("interview_report", INTERVIEW_REPORT_V1),
    ("ceo_brief", CEO_BRIEF_V1),
    ("rejection_message", REJECTION_MESSAGE_V1),
    ("score_open_text", SCORE_OPEN_TEXT_V1),
    ("voice_screen_gen", VOICE_SCREEN_GEN_V1),
    ("voice_screen_eval", VOICE_SCREEN_EVAL_V1),
    ("role_draft_system", ROLE_DRAFT_SYSTEM),
    ("section_rewrite_system", SECTION_REWRITE_SYSTEM),
    ("linkedin_post_system", LINKEDIN_POST_SYSTEM),
    # Agent prompts (only assignment_gen — chat prompts disabled, voice-only)
    ("assignment_gen", ASSIGNMENT_GEN_V1),
]


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

    for name, text in PROMPTS:
        try:
            lf.create_prompt(
                name=name,
                prompt=text,
                type="text",
                labels=["production"],
            )
            print(f"  [OK] {name}")
            success += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            failed += 1

    lf.flush()
    print(f"\nDone. Uploaded: {success}, Failed: {failed}, Total: {len(PROMPTS)}")
    print("\nNext steps:")
    print("  1. Open Langfuse -> Prompts to see all uploaded prompts")
    print("  2. Invite team members: Settings -> Members -> Add by email")
    print("  3. Team can now edit prompts in browser — changes go live within 60s")


if __name__ == "__main__":
    main()
