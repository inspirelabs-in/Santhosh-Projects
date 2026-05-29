"""V2 chat token: the apply-token helpers must accept the ``"chat"`` action.

The candidate-facing chat link goes through ``generate_apply_token`` /
``verify_apply_token`` with a new ``action="chat"`` claim. This test pins
that action so a future refactor of ``ApplyTokenClaims`` cannot quietly
drop chat support.
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from src.services.screening_url import (
    InvalidScreeningToken,
    generate_apply_token,
    verify_apply_token,
)


def test_chat_action_roundtrip():
    app_id = uuid4()
    token = generate_apply_token(application_id=app_id, action="chat")
    claims = verify_apply_token(token)
    assert claims.action == "chat"
    assert claims.application_id == app_id


def test_screening_action_still_accepted():
    token = generate_apply_token(application_id=uuid4(), action="screening")
    assert verify_apply_token(token).action == "screening"


def test_assignment_action_still_accepted():
    token = generate_apply_token(application_id=uuid4(), action="assignment")
    assert verify_apply_token(token).action == "assignment"


def test_unknown_action_rejected_at_issue_time():
    with pytest.raises(ValueError):
        generate_apply_token(application_id=uuid4(), action="impersonate")


def test_expired_chat_token_rejected():
    token = generate_apply_token(
        application_id=uuid4(), action="chat", ttl_days=0
    )
    time.sleep(1.0)
    with pytest.raises(InvalidScreeningToken):
        verify_apply_token(token)


def test_tampered_chat_token_rejected():
    token = generate_apply_token(application_id=uuid4(), action="chat")
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(InvalidScreeningToken):
        verify_apply_token(tampered)
