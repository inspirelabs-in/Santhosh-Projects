"""Prompt versioning for agent generators.

Every constant here is suffixed with a version (`_V1`) and re-exported with
a paired version string so audit_log + Langfuse can record exactly which
prompt produced an output.
"""

from src.agent.prompts.assignment import ASSIGNMENT_GEN_V1, ASSIGNMENT_GEN_VERSION
from src.agent.prompts.extract import EXTRACT_TURN_V1, EXTRACT_TURN_VERSION
from src.agent.prompts.tailored_qs import TAILORED_QS_V1, TAILORED_QS_VERSION

__all__ = [
    "ASSIGNMENT_GEN_V1",
    "ASSIGNMENT_GEN_VERSION",
    "EXTRACT_TURN_V1",
    "EXTRACT_TURN_VERSION",
    "TAILORED_QS_V1",
    "TAILORED_QS_VERSION",
]
