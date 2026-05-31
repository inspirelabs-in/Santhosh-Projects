"""Tool availability + degradation under missing creds."""
from __future__ import annotations

import pytest

from grabon_intel.config import get_settings


def _clear() -> None:
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_searxng_available_when_url_set(monkeypatch) -> None:
    from grabon_intel.tools.searxng import SearXNGTool

    monkeypatch.setenv("SEARXNG_URL", "http://localhost:8888")
    _clear()
    assert SearXNGTool().available is True


def test_searxng_unavailable_when_url_missing(monkeypatch) -> None:
    from grabon_intel.tools.searxng import SearXNGTool

    monkeypatch.setenv("SEARXNG_URL", "")
    _clear()
    assert SearXNGTool().available is False


def test_wappalyzer_available_when_module_installed() -> None:
    from grabon_intel.tools.wappalyzer_local import WappalyzerLocalTool

    # Module is a project dep, so this is True in CI.
    assert WappalyzerLocalTool().available is True


def test_keyless_tools_always_available() -> None:
    from grabon_intel.tools.lighthouse import LighthouseTool
    from grabon_intel.tools.news import GoogleNewsTool
    from grabon_intel.tools.rdap import RDAPTool
    from grabon_intel.tools.website import WebsiteFetchTool

    assert GoogleNewsTool().available is True
    assert WebsiteFetchTool().available is True
    assert LighthouseTool().available is True
    assert RDAPTool().available is True


@pytest.mark.asyncio
async def test_searxng_safe_degrades_without_url(monkeypatch) -> None:
    monkeypatch.setenv("SEARXNG_URL", "")
    _clear()
    from grabon_intel.tools.searxng import SearXNGTool

    r = await SearXNGTool().safe(query="hello")
    assert r.degraded is True
    assert r.data is None
