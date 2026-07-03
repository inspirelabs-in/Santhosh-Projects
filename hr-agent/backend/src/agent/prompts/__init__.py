"""Prompt versioning for agent generators.

Every constant here is suffixed with a version (`_V1`) and re-exported with
a paired version string so audit_log + Langfuse can record exactly which
prompt produced an output.
"""

from src.agent.prompts.assignment import ASSIGNMENT_GEN_V1, ASSIGNMENT_GEN_VERSION

__all__ = [
    "ASSIGNMENT_GEN_V1",
    "ASSIGNMENT_GEN_VERSION",
]
