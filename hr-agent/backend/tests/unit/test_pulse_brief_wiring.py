"""Guards the recruiter-brief plumbing for Pulse assignment generation.

Root cause these tests pin: the recruiter's typed assignment brief lives on the
conversation's draft artifact. The assignment generator only honors it if the
*conversation id* reaches the tool. Previously ``call_tool`` injected the
conversation id only for ``propose_role_draft`` (via a contextvar), so
``generate_assignment_for_role`` / ``create_role_with_assignment`` never saw the
brief and fell back to the LLM's unreliable ``user_brief`` -> generic problems
that ignored what the recruiter actually asked for.

The fix injects the (system-supplied, never LLM-supplied) conversation id as an
explicit kwarg for the brief-aware tools, mirroring how ``actor_hash`` is
injected, and makes the brief resolver prefer the recruiter's brief over the
agent's.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.recruiter_agent import tools


# --- fakes ----------------------------------------------------------------


class _FakeSession:
    def __init__(self, role):
        self._role = role

    async def get(self, _model, _pk):
        return self._role


class _FakeScope:
    def __init__(self, role):
        self._role = role

    async def __aenter__(self):
        return _FakeSession(self._role)

    async def __aexit__(self, *_exc):
        return False


def _fake_session_scope(role):
    def _factory():
        return _FakeScope(role)

    return _factory


# --- tests ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_injects_conversation_id_for_assignment_tools(monkeypatch):
    """The brief-aware tools receive the system conversation id as an explicit arg."""
    captured: dict[str, object] = {}

    async def _stub(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    for name in (
        "generate_assignment_for_role",
        "create_role_with_assignment",
        "ensure_role_assignment",
    ):
        captured.clear()
        monkeypatch.setitem(tools.TOOLS, name, _stub)
        await tools.call_tool(name, {"role_id": "r1"}, conversation_id="conv-123")
        assert captured.get("conversation_id") == "conv-123", name


@pytest.mark.asyncio
async def test_call_tool_does_not_inject_conversation_id_for_other_tools(monkeypatch):
    """Read-only tools that don't touch the draft artifact stay untouched."""
    captured: dict[str, object] = {}

    async def _stub(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setitem(tools.TOOLS, "list_roles", _stub)
    await tools.call_tool("list_roles", {}, conversation_id="conv-123")
    assert "conversation_id" not in captured


@pytest.mark.asyncio
async def test_draft_brief_resolver_uses_explicit_conversation_id(monkeypatch):
    conv_id = str(uuid4())
    artifact = SimpleNamespace(content={"assignment": {"brief": "Build a DSA visualizer"}})

    import src.db.repositories.artifact as artifact_repo

    async def _get_active(_session, cid):
        assert str(cid) == conv_id
        return artifact

    monkeypatch.setattr(tools, "session_scope", _fake_session_scope(None))
    monkeypatch.setattr(artifact_repo, "get_active_for_conversation", _get_active)

    out = await tools._draft_brief_for_active_conversation(conv_id)
    assert out == "Build a DSA visualizer"


@pytest.mark.asyncio
async def test_generate_assignment_grounds_on_artifact_brief_when_role_unset(monkeypatch):
    """The reported bug: agent passes a hallucinated brief, the role has no saved
    brief yet, but the recruiter typed one in the panel -> the panel brief wins."""
    conv_id = str(uuid4())
    role = SimpleNamespace(
        title="Backend Engineer",
        jd_text="x" * 120,
        assignment_brief="",
        evaluation_spec=None,
        company_context=None,
        status="draft",
    )
    monkeypatch.setattr(tools, "session_scope", _fake_session_scope(role))

    async def _draft_brief(cid=None):
        assert cid == conv_id
        return "Build a DSA visualizer"

    monkeypatch.setattr(tools, "_draft_brief_for_active_conversation", _draft_brief)

    captured: dict[str, object] = {}

    async def _fake_gen(**kwargs):
        captured["user_brief"] = kwargs.get("user_brief")
        return SimpleNamespace(
            model_dump=lambda: {
                "problems": [{"title": "p"}],
                "brief_md": "md",
                "submission_format": {},
                "evaluation_rubric": {},
            }
        )

    monkeypatch.setattr("src.agent.generators.gen_assignment", _fake_gen)

    async def _fake_persist(**_kwargs):
        return {"ok": True}

    monkeypatch.setattr(tools, "_persist_assignment", _fake_persist)

    await tools.generate_assignment_for_role(
        role_id=str(uuid4()),
        n_problems=1,
        conversation_id=conv_id,
        user_brief="nodejs + typescript",  # hallucinated agent value
    )
    assert captured["user_brief"] == "Build a DSA visualizer"


@pytest.mark.asyncio
async def test_create_role_with_assignment_prefers_artifact_brief(monkeypatch):
    """A hallucinated agent brief must never overwrite the recruiter's panel brief."""
    conv_id = str(uuid4())
    captured: dict[str, object] = {}

    async def _fake_create_role(**kwargs):
        return {"id": str(uuid4()), "title": kwargs.get("title")}

    monkeypatch.setattr(tools, "create_role", _fake_create_role)

    async def _draft_brief(cid=None):
        assert cid == conv_id
        return "Build a DSA visualizer"

    monkeypatch.setattr(tools, "_draft_brief_for_active_conversation", _draft_brief)

    captured_brief = {}

    class _Role:
        id = uuid4()
        assignment_brief = None
        assignment_problem_doc_key = None
        status = "open"

    role_obj = _Role()

    async def _stub_stage_for_role(*_a, **_k):
        return []

    # The function persists assignment_brief on the role inside a session.
    monkeypatch.setattr(tools, "session_scope", _fake_session_scope(role_obj))
    import src.db.repositories.role_pipeline_stage as stage_repo

    monkeypatch.setattr(stage_repo, "for_role", _stub_stage_for_role)

    await tools.create_role_with_assignment(
        title="Backend Engineer",
        jd_text="x" * 120,
        conversation_id=conv_id,
        brief="nodejs + typescript",  # hallucinated agent value
    )
    assert role_obj.assignment_brief == "Build a DSA visualizer"


class _SelectiveSession:
    """Returns the role only for its real id; None for any other id (e.g. a draft id)."""

    def __init__(self, role, real_rid):
        self._role = role
        self._rid = real_rid

    async def get(self, _model, pk):
        return self._role if pk == self._rid else None


class _SelectiveScope:
    def __init__(self, role, real_rid):
        self._role = role
        self._rid = real_rid

    async def __aenter__(self):
        return _SelectiveSession(self._role, self._rid)

    async def __aexit__(self, *_exc):
        return False


@pytest.mark.asyncio
async def test_generate_resolves_role_when_agent_passes_draft_id(monkeypatch):
    """The reported hallucination: agent passes the DRAFT artifact id as role_id.
    The tool must resolve the real applied role from the conversation, not error."""
    real_rid = uuid4()
    draft_id = uuid4()  # what the agent wrongly passes as role_id
    conv_id = str(uuid4())
    role = SimpleNamespace(
        title="Software Developer", jd_text="x" * 120,
        assignment_brief="1. coupon mgmt\n2. CRM with AI", evaluation_spec=None,
        company_context=None, status="draft",
    )

    def _scope():
        return _SelectiveScope(role, real_rid)

    monkeypatch.setattr(tools, "session_scope", _scope)

    async def _resolver(cid=None):
        assert cid == conv_id
        return real_rid

    monkeypatch.setattr(tools, "_applied_role_for_active_conversation", _resolver)

    captured = {}

    async def _fake_gen(**kwargs):
        captured["user_brief"] = kwargs.get("user_brief")
        return SimpleNamespace(model_dump=lambda: {"problems": [{"title": "p"}], "brief_md": "md", "submission_format": {}, "evaluation_rubric": {}})

    monkeypatch.setattr("src.agent.generators.gen_assignment", _fake_gen)

    async def _fake_persist(*, rid, **_kw):
        captured["persist_rid"] = rid
        return {"ok": True}

    monkeypatch.setattr(tools, "_persist_assignment", _fake_persist)

    res = await tools.generate_assignment_for_role(
        role_id=str(draft_id),       # WRONG id (the draft artifact id)
        n_problems=2,
        conversation_id=conv_id,
    )
    assert res.get("ok") is True
    assert captured["persist_rid"] == real_rid          # resolved, not the draft id
    assert captured["user_brief"] == "1. coupon mgmt\n2. CRM with AI"  # role brief grounded


def test_compile_prompt_warns_on_dropped_variable(monkeypatch, caplog):
    """A supplied variable with no slot in the template is the silent-drop trap that
    hid the stale-prompt bug. It must now warn (and still substitute present ones)."""
    import logging
    from src.llm import prompt_manager as pm

    monkeypatch.setattr(pm, "get_prompt", lambda name, *, fallback, label="production": "hello {a}")
    with caplog.at_level(logging.WARNING, logger="src.llm.prompt_manager"):
        out = pm.compile_prompt("x", fallback="", a="X", b="DROPPED_VAR")
    assert out == "hello X"
    assert any("DROPPED" in r.getMessage() and "b" in r.getMessage() for r in caplog.records)
