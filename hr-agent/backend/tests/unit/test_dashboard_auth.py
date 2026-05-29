"""Dashboard key → role resolution tests (pure auth logic)."""

from __future__ import annotations

import importlib

import pytest
from fastapi import HTTPException


def _reload_auth(monkeypatch, keys_json: str):
    monkeypatch.setenv("DASHBOARD_KEYS", keys_json)
    import src.api.auth as auth_mod

    return importlib.reload(auth_mod)


def test_missing_key_returns_401(monkeypatch):
    auth = _reload_auth(monkeypatch, '{"abc": "admin"}')
    dep = auth.require_viewer
    with pytest.raises(HTTPException) as exc:
        dep(x_dashboard_key=None)
    assert exc.value.status_code == 401


def test_unknown_key_returns_401(monkeypatch):
    auth = _reload_auth(monkeypatch, '{"abc": "admin"}')
    with pytest.raises(HTTPException) as exc:
        auth.require_viewer(x_dashboard_key="wrong")
    assert exc.value.status_code == 401


def test_viewer_cannot_write(monkeypatch):
    auth = _reload_auth(monkeypatch, '{"viewer-1": "viewer"}')
    # Recruiter endpoint should reject a viewer key.
    with pytest.raises(HTTPException) as exc:
        auth.require_recruiter(x_dashboard_key="viewer-1")
    assert exc.value.status_code == 403


def test_admin_can_do_everything(monkeypatch):
    auth = _reload_auth(monkeypatch, '{"a": "admin"}')
    assert auth.require_viewer(x_dashboard_key="a") == "admin"
    assert auth.require_recruiter(x_dashboard_key="a") == "admin"
    assert auth.require_admin(x_dashboard_key="a") == "admin"


def test_invalid_role_in_config_ignored(monkeypatch):
    auth = _reload_auth(monkeypatch, '{"a": "superuser", "b": "viewer"}')
    with pytest.raises(HTTPException):
        auth.require_viewer(x_dashboard_key="a")
    assert auth.require_viewer(x_dashboard_key="b") == "viewer"
