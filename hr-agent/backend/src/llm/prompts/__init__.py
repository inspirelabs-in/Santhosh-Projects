"""Versioned prompt templates.

Every template is a module-level string constant with a version suffix (e.g.
CLASSIFY_EMAIL_V1). Langfuse records which version ran; git is the source of
truth. Bump the version when the content changes -- never mutate in place.
"""

from src.llm.prompts.assignment_parse import ASSIGNMENT_PARSE_V1, ASSIGNMENT_PARSE_VERSION
from src.llm.prompts.classify_email import CLASSIFY_EMAIL_V1, CLASSIFY_EMAIL_VERSION
from src.llm.prompts.fit_score import FIT_SCORE_V1, FIT_SCORE_VERSION
from src.llm.prompts.interview_report import INTERVIEW_REPORT_V1, INTERVIEW_REPORT_VERSION
from src.llm.prompts.journey_report import JOURNEY_REPORT_V1, JOURNEY_REPORT_VERSION
from src.llm.prompts.parse_resume import PARSE_RESUME_V1, PARSE_RESUME_VERSION
from src.llm.prompts.rejection_message import REJECTION_MESSAGE_V1, REJECTION_MESSAGE_VERSION
from src.llm.prompts.score_open_text import SCORE_OPEN_TEXT_V1, SCORE_OPEN_TEXT_VERSION
from src.llm.prompts.screening_eval import SCREENING_EVAL_V1, SCREENING_EVAL_VERSION
from src.llm.prompts.screening_gen import SCREENING_GEN_V1, SCREENING_GEN_VERSION

__all__ = [
    "ASSIGNMENT_PARSE_V1",
    "ASSIGNMENT_PARSE_VERSION",
    "CLASSIFY_EMAIL_V1",
    "CLASSIFY_EMAIL_VERSION",
    "FIT_SCORE_V1",
    "FIT_SCORE_VERSION",
    "INTERVIEW_REPORT_V1",
    "INTERVIEW_REPORT_VERSION",
    "JOURNEY_REPORT_V1",
    "JOURNEY_REPORT_VERSION",
    "PARSE_RESUME_V1",
    "PARSE_RESUME_VERSION",
    "REJECTION_MESSAGE_V1",
    "REJECTION_MESSAGE_VERSION",
    "SCORE_OPEN_TEXT_V1",
    "SCORE_OPEN_TEXT_VERSION",
    "SCREENING_EVAL_V1",
    "SCREENING_EVAL_VERSION",
    "SCREENING_GEN_V1",
    "SCREENING_GEN_VERSION",
]
