"""Tool-level RBAC.

Each tool declares the minimum recruiter role required to invoke it. The
agent runner consults this map before dispatching a call. Insufficient ->
return ``{"error": "permission_denied"}`` so Pulse surfaces a clean refusal
instead of crashing on the SQL layer.
"""

from __future__ import annotations

from typing import Literal

Role = Literal["viewer", "recruiter", "admin"]


# Role precedence: admin > recruiter > viewer.
_ORDER: dict[str, int] = {"viewer": 0, "recruiter": 1, "admin": 2}


# All tools known to the runner. The default if a tool isn't listed is
# "recruiter" -- safer than letting an unknown tool fall through to viewer.
TOOL_ROLES: dict[str, Role] = {
    # Reads
    "list_candidates": "viewer",
    "get_candidate": "viewer",
    "list_roles": "viewer",
    "pipeline_metrics": "viewer",
    "stuck_applications": "viewer",
    "audit_tail": "viewer",
    "search_candidates": "viewer",
    "get_journey_report": "viewer",
    "metrics_period": "viewer",
    "list_meetings": "viewer",
    "list_voice_calls": "viewer",
    "read_audit": "viewer",
    "recall": "viewer",
    "smart_defaults_for_role": "viewer",
    "generate_assignment_for_role": "recruiter",
    "draft_linkedin_post": "recruiter",
    "publish_linkedin_post": "recruiter",
    # Recruiter writes
    "trigger_chat_invite": "recruiter",
    "create_role": "recruiter",
    "create_role_with_assignment": "recruiter",
    "update_role": "recruiter",
    "archive_role": "recruiter",
    "set_role_assignment_brief": "recruiter",
    "override_stage": "recruiter",
    "send_custom_email": "recruiter",
    "add_candidate_note": "recruiter",
    "schedule_interview": "recruiter",
    "propose_slots": "recruiter",
    "set_panel_member": "recruiter",
    "parse_attachment": "recruiter",
    "remember": "recruiter",
    # Admin
    "update_setting": "admin",
}


# Tools whose effects are visible outside the recruiter's chat (emails,
# stage flips, role mutations) require an explicit user confirm before
# they execute. Reads + memory + recruiter-private actions are exempt.
CONFIRM_REQUIRED: frozenset[str] = frozenset(
    {
        "trigger_chat_invite",
        "create_role",
        "create_role_with_assignment",
        "update_role",
        "archive_role",
        "set_role_assignment_brief",
        "override_stage",
        "send_custom_email",
        "schedule_interview",
        "set_panel_member",
        "update_setting",
        "publish_linkedin_post",  # external broadcast -- always confirm
    }
)


def can(actor_role: str, tool_name: str) -> bool:
    """Return True iff ``actor_role`` is at or above the tool's required level.

    Unknown tools default to ``recruiter`` so a forgotten registration
    can't get accidentally invoked by a viewer.
    """
    required = TOOL_ROLES.get(tool_name, "recruiter")
    return _ORDER.get(actor_role, -1) >= _ORDER[required]


def required_role(tool_name: str) -> Role:
    return TOOL_ROLES.get(tool_name, "recruiter")


def needs_confirm(tool_name: str) -> bool:
    return tool_name in CONFIRM_REQUIRED
