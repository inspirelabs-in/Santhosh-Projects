"""Loader copy for Pulse's "thinking" indicator.

Single source of truth for what the recruiter sees while Pulse works. The runner
emits a ``thinking`` SSE event with one of these labels right before it runs a
tool, so the spinner reads like the agent is actually doing that thing instead of
a flat "thinking".

Two pools:
  * ``TOOL_LOADERS`` -- a specific phrase per tool. The label names the real work
    ("Drafting the role...", "Combing the talent pool...").
  * ``GENERIC_LOADERS`` -- short intellectual filler shown for un-mapped tools and
    for the between-steps beats (reading the request, processing an answer).

``loader_for(name)`` returns the tool's phrase, falling back to a random generic
one so an unmapped tool never shows a raw function name.
"""

from __future__ import annotations

import random

# Per-tool loader copy. Keyed by the exact tool name in ``tools.TOOLS``.
TOOL_LOADERS: dict[str, str] = {
    # ---- Reads ----
    "list_candidates": "Pulling up candidates…",
    "get_candidate": "Opening the candidate file…",
    "search_candidates": "Searching candidates…",
    "search_talent_pool": "Combing the talent pool…",
    "list_roles": "Pulling up roles…",
    "get_role": "Opening the role…",
    "pipeline_metrics": "Crunching pipeline numbers…",
    "metrics_period": "Crunching the numbers…",
    "stuck_applications": "Finding what's stalled…",
    "audit_tail": "Reading the audit trail…",
    "read_audit": "Reading the audit trail…",
    "get_journey_report": "Tracing the candidate's journey…",
    "list_meetings": "Checking the calendar…",
    "list_voice_calls": "Reviewing voice screens…",
    "recall": "Searching my memory…",
    "smart_defaults_for_role": "Studying similar roles…",
    "parse_attachment": "Reading the attachment…",
    "propose_slots": "Finding open slots…",
    "suggest_meeting_slots": "Finding open slots…",
    # ---- Role / JD drafting ----
    "propose_role_draft": "Drafting the role…",
    "create_role": "Creating the role…",
    "create_role_with_assignment": "Drafting the role and assignment…",
    "update_role": "Updating the role…",
    "archive_role": "Archiving the role…",
    # ---- Assignment ----
    "generate_assignment_for_role": "Designing the assignment…",
    "set_role_assignment_brief": "Saving the assignment brief…",
    # ---- Candidate actions ----
    "override_stage": "Moving the candidate…",
    "add_candidate_note": "Adding your note…",
    "send_custom_email": "Drafting the email…",
    # ---- Scheduling ----
    "schedule_interview": "Setting up the interview…",
    "schedule_meeting": "Setting up the meeting…",
    "reschedule_meeting": "Rescheduling…",
    "set_panel_member": "Updating the panel…",
    "add_panel_member": "Updating the panel…",
    # ---- LinkedIn ----
    "draft_linkedin_post": "Writing the post…",
    "publish_linkedin_post": "Publishing the post…",
    # ---- Misc ----
    "remember": "Noting that down…",
    "update_setting": "Updating settings…",
}

# Short, intellectual filler for un-mapped tools and between-step beats.
GENERIC_LOADERS: list[str] = [
    "Thinking it through…",
    "Connecting the dots…",
    "Reasoning about it…",
    "Working it out…",
    "Lining things up…",
    "Reading the room…",
    "Making sense of it…",
    "Weighing the options…",
    "Putting it together…",
    "Getting my bearings…",
]


def loader_for(tool_name: str) -> str:
    """Loader label for a tool call. Falls back to a random generic phrase."""
    phrase = TOOL_LOADERS.get(tool_name)
    if phrase:
        return phrase
    return generic_loader()


def generic_loader() -> str:
    """A random intellectual filler line for non-tool beats."""
    return random.choice(GENERIC_LOADERS)
