"""Veto rule evaluation (logic only, no DB)."""
from __future__ import annotations

from dataclasses import dataclass


# We exercise the logic directly by emulating the DB rows. Avoids needing
# a live Postgres in unit tests.

import pytest
from sqlalchemy import text

# Direct logic copy avoided — we re-test through evaluate() with a fake session.
import grabon_intel.veto as veto


class _FakeSession:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    async def execute(self, _sql, _params=None):
        class _R:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

            def first(self):
                return self._rows[0] if self._rows else None

            def mappings(self):
                class _M:
                    def __init__(self, rs):
                        self._rs = rs

                    def all(self):
                        return self._rs

                    def first(self):
                        return self._rs[0] if self._rs else None

                return _M(self._rows)

        return _R(self._rows)


@pytest.mark.asyncio
async def test_block_exact_domain_match() -> None:
    s = _FakeSession([(1, "block", "competitor.com", "domain", "competitor")])
    r = await veto.evaluate(s, name=None, domain="competitor.com")
    assert r.decision == "blocked"


@pytest.mark.asyncio
async def test_existing_client_priority_over_block() -> None:
    s = _FakeSession([
        (1, "block", "*.com", "domain", "any"),
        (2, "existing_client", "client.com", "domain", "active client"),
    ])
    r = await veto.evaluate(s, name=None, domain="client.com")
    assert r.decision == "existing_client"


@pytest.mark.asyncio
async def test_allowlist_mode_blocks_non_matches() -> None:
    s = _FakeSession([(1, "allow", "*.in", "domain", "India only")])
    r = await veto.evaluate(s, name=None, domain="brand.com")
    assert r.decision == "blocked"


@pytest.mark.asyncio
async def test_allowlist_mode_passes_match() -> None:
    s = _FakeSession([(1, "allow", "*.in", "domain", "India only")])
    r = await veto.evaluate(s, name=None, domain="brand.in")
    assert r.decision == "allowed"


@pytest.mark.asyncio
async def test_name_scope_substring() -> None:
    s = _FakeSession([(1, "block", "competitor", "name", None)])
    r = await veto.evaluate(s, name="Some Competitor Inc", domain=None)
    assert r.decision == "blocked"


@pytest.mark.asyncio
async def test_no_rules_allows() -> None:
    s = _FakeSession([])
    r = await veto.evaluate(s, name="anything", domain="anywhere.com")
    assert r.decision == "allowed"
