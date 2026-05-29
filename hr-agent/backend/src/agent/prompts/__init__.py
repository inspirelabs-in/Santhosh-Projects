"""Prompt versioning for the V2 chat agent.

Every constant here is suffixed with a version (`_V1`) and re-exported with
a paired version string so audit_log + Langfuse can record exactly which
prompt produced an output.
"""

from src.agent.prompts.assignment import ASSIGNMENT_GEN_V1, ASSIGNMENT_GEN_VERSION
from src.agent.prompts.chat_turn import CHAT_TURN_SYSTEM_V1, CHAT_TURN_VERSION
from src.agent.prompts.extract import EXTRACT_TURN_V1, EXTRACT_TURN_VERSION
from src.agent.prompts.tailored_qs import TAILORED_QS_V1, TAILORED_QS_VERSION

__all__ = [
    "ASSIGNMENT_GEN_V1",
    "ASSIGNMENT_GEN_VERSION",
    "CHAT_TURN_SYSTEM_V1",
    "CHAT_TURN_VERSION",
    "EXTRACT_TURN_V1",
    "EXTRACT_TURN_VERSION",
    "TAILORED_QS_V1",
    "TAILORED_QS_VERSION",
]
