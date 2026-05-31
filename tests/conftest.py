"""Test fixtures. DB tests are opt-in via GRABON_TEST_DATABASE_URL."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def db_url() -> str | None:
    return os.getenv("GRABON_TEST_DATABASE_URL")
