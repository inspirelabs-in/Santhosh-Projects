"""Pulse tool RBAC + confirm-required tagging."""

from __future__ import annotations

import pytest

from src.recruiter_agent.rbac import (
    CONFIRM_REQUIRED,
    TOOL_ROLES,
    can,
    needs_confirm,
    required_role,
)


def test_viewer_can_read_only():
    for tool in (
        "list_candidates",
        "get_candidate",
        "list_roles",
        "pipeline_metrics",
        "audit_tail",
        "search_candidates",
    ):
        assert can("viewer", tool), tool


def test_viewer_cannot_write():
    for tool in (
        "create_role",
        "override_stage",
        "send_custom_email",
        "trigger_chat_invite",
        "schedule_interview",
        "set_panel_member",
    ):
        assert not can("viewer", tool), tool


def test_recruiter_can_write_but_not_admin():
    for tool in ("create_role", "override_stage", "send_custom_email", "trigger_chat_invite"):
        assert can("recruiter", tool), tool
    # update_setting is admin-only.
    assert not can("recruiter", "update_setting")


def test_admin_can_everything():
    for tool in TOOL_ROLES:
        assert can("admin", tool), tool


def test_unknown_tool_defaults_recruiter():
    """Forgotten registration -> default to recruiter (safer than viewer)."""
    assert not can("viewer", "this_tool_does_not_exist")
    assert can("recruiter", "this_tool_does_not_exist")


def test_required_role_lookup():
    assert required_role("get_candidate") == "viewer"
    assert required_role("create_role") == "recruiter"
    assert required_role("update_setting") == "admin"


def test_destructive_tools_are_confirm_required():
    must_confirm = {
        "trigger_chat_invite",
        "create_role",
        "update_role",
        "archive_role",
        "set_role_assignment_brief",
        "override_stage",
        "send_custom_email",
        "schedule_interview",
        "set_panel_member",
        "update_setting",
    }
    assert must_confirm.issubset(CONFIRM_REQUIRED)
    for t in must_confirm:
        assert needs_confirm(t), t


def test_read_tools_skip_confirm():
    for t in ("list_candidates", "get_candidate", "pipeline_metrics", "search_candidates", "recall"):
        assert not needs_confirm(t), t
