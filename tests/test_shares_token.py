"""Trace-share signed token sign/verify."""
from __future__ import annotations

import datetime as dt
import time

import pytest


@pytest.fixture(autouse=True)
def _api_keys(monkeypatch):
    monkeypatch.setenv("GRABON_API_KEYS", "k1,k2")
    from grabon_intel.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_make_and_parse_round_trip() -> None:
    from grabon_intel.api.routes.shares import make_token, parse_token

    token, expires = make_token("11111111-1111-1111-1111-111111111111", ttl_hours=1)
    parsed = parse_token(token)
    assert parsed is not None
    trace_id, exp = parsed
    assert trace_id == "11111111-1111-1111-1111-111111111111"
    assert exp.timestamp() == pytest.approx(expires.timestamp(), abs=1)


def test_tampered_token_rejected() -> None:
    from grabon_intel.api.routes.shares import make_token, parse_token

    token, _ = make_token("abc", ttl_hours=1)
    # Flip one base64 char.
    bad = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    assert parse_token(bad) is None


def test_expired_token_rejected(monkeypatch) -> None:
    from grabon_intel.api.routes.shares import make_token, parse_token

    token, _ = make_token("abc", ttl_hours=1)
    # Move clock forward 2h.
    import grabon_intel.api.routes.shares as shares_mod

    class _LaterDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime.fromtimestamp(time.time() + 3 * 3600, tz=tz)

    monkeypatch.setattr(shares_mod.dt, "datetime", _LaterDT)
    assert parse_token(token) is None
