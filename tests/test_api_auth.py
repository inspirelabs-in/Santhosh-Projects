"""FastAPI gateway auth + basic routing."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("GRABON_API_KEYS", "good-key-1,good-key-2")
    monkeypatch.setenv("GRABON_API_CORS_ORIGINS", "http://localhost:3000")
    from grabon_intel.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    from grabon_intel.api import create_app

    return TestClient(create_app())


def test_health_open(client) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_protected_requires_key(client) -> None:
    r = client.get("/me")
    assert r.status_code == 401


def test_protected_rejects_bad_key(client) -> None:
    r = client.get("/me", headers={"X-API-Key": "nope"})
    assert r.status_code == 403


def test_protected_accepts_good_key(client) -> None:
    r = client.get("/me", headers={"X-API-Key": "good-key-1"})
    assert r.status_code == 200


def test_brands_route_requires_auth(client) -> None:
    r = client.get("/brands")
    assert r.status_code == 401
