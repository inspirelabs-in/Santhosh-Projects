"""Events HMAC signing + verification."""
from __future__ import annotations

import json

from grabon_intel.events import _sign, verify_signature


def test_sign_is_deterministic() -> None:
    a = _sign("secret", "body")
    b = _sign("secret", "body")
    assert a == b
    assert _sign("other", "body") != a


def test_verify_round_trip() -> None:
    body = json.dumps({"event": "x", "payload": {"k": 1}})
    sig = _sign("topsecret", body)
    assert verify_signature("topsecret", body, f"sha256={sig}") is True
    assert verify_signature("topsecret", body, sig) is True  # bare sig also accepted
    assert verify_signature("wrong", body, sig) is False
    assert verify_signature("topsecret", body, None) is False
    assert verify_signature("topsecret", body, "sha256=deadbeef") is False
