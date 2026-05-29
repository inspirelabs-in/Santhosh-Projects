"""Attachment parsers used by the ``parse_attachment`` recruiter tool.

Supports:
  * PDF (PyMuPDF)
  * DOCX (python-docx)
  * Plain text (.txt, .md)
  * URL (httpx GET, whitelist domains, strips boilerplate)

The returned text is bounded to ``MAX_CHARS`` so a 200-page PDF can't blow
the agent's context budget. The tool caller decides what to do with it
(e.g. feed to ``create_role`` as JD text, or pre-fill a candidate from a
resume).
"""

from __future__ import annotations

import io
import logging
import re
from typing import Literal
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

MAX_CHARS = 16_000

ALLOWED_URL_DOMAINS: tuple[str, ...] = (
    "linkedin.com",
    "github.com",
    "gist.github.com",
    "raw.githubusercontent.com",
    "google.com",  # docs.google.com etc.
    ".gov",
    ".edu",
)


Kind = Literal["pdf", "docx", "text", "url"]


def _truncate(s: str) -> str:
    return s if len(s) <= MAX_CHARS else s[:MAX_CHARS] + "\n...[truncated]"


def parse_pdf(data: bytes) -> str:
    """Extract text from a PDF byte buffer."""
    try:
        import pymupdf  # type: ignore
    except ImportError:  # pragma: no cover
        return "[pymupdf not installed]"
    parts: list[str] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            parts.append(page.get_text("text"))
    return _truncate("\n".join(parts).strip())


def parse_docx(data: bytes) -> str:
    """Extract text from a .docx byte buffer."""
    try:
        import docx  # type: ignore
    except ImportError:  # pragma: no cover
        return "[python-docx not installed]"
    f = io.BytesIO(data)
    document = docx.Document(f)
    parts = [p.text for p in document.paragraphs if p.text]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return _truncate("\n".join(parts).strip())


def parse_text(data: bytes, encoding: str = "utf-8") -> str:
    return _truncate(data.decode(encoding, errors="replace"))


def _domain_allowed(host: str) -> bool:
    host = host.lower()
    for allowed in ALLOWED_URL_DOMAINS:
        if allowed.startswith("."):
            if host.endswith(allowed):
                return True
        else:
            if host == allowed or host.endswith("." + allowed):
                return True
    return False


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


async def fetch_url(url: str, timeout_seconds: float = 8.0) -> dict[str, str]:
    """GET a URL from the whitelist, return ``{text, content_type, url}``.

    Strips script/style + html tags + collapses whitespace. Will not follow
    redirects to off-whitelist domains.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"error": "unsupported_scheme", "url": url}
    if not _domain_allowed(parsed.hostname or ""):
        return {"error": "domain_not_allowed", "url": url}

    async with httpx.AsyncClient(
        follow_redirects=False, timeout=timeout_seconds, headers={"User-Agent": "PulseRecruiter/1.0"}
    ) as client:
        try:
            r = await client.get(url)
        except httpx.HTTPError as e:
            return {"error": f"fetch_failed: {e}", "url": url}
    content_type = r.headers.get("content-type", "")
    body = r.text or ""
    if "html" in content_type.lower():
        body = _SCRIPT_RE.sub(" ", body)
        body = _TAG_RE.sub(" ", body)
        body = _WS_RE.sub(" ", body).strip()
    return {
        "url": str(r.url),
        "content_type": content_type,
        "text": _truncate(body),
        "status": str(r.status_code),
    }


async def parse_bytes(*, kind: str, data: bytes) -> dict[str, str]:
    """Dispatch by kind. Returns ``{kind, text}`` or ``{error}``."""
    k = kind.lower()
    try:
        if k in ("pdf", "application/pdf"):
            return {"kind": "pdf", "text": parse_pdf(data)}
        if k in ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
            return {"kind": "docx", "text": parse_docx(data)}
        if k in ("text", "txt", "md", "text/plain", "text/markdown"):
            return {"kind": "text", "text": parse_text(data)}
    except Exception as e:  # noqa: BLE001
        logger.exception("parse_bytes failed for %s", kind)
        return {"error": f"parse_failed: {e}"}
    return {"error": f"unsupported_kind: {kind}"}
