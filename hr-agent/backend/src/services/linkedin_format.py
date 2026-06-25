"""Render an existing JD as LinkedIn-ready plain text.

The role draft already writes a vibey, LinkedIn-ready JD (with its own
"How to apply" block). Posting to LinkedIn therefore REUSES that saved JD
verbatim -- there is no separate LLM generation. LinkedIn does not render
markdown, so this does a small, deterministic markdown -> plain-text cleanup
only. No model call, no new content, no bias.
"""

from __future__ import annotations

import re


def jd_to_linkedin_text(jd_text: str | None) -> str:
    """Strip markdown syntax from a JD so it reads cleanly when pasted to
    LinkedIn. Deterministic and content-preserving: it removes heading hashes,
    emphasis markers, and link syntax, and normalises bullets and blank lines.
    It never rewrites or summarises the text."""
    if not jd_text:
        return ""
    out_lines: list[str] = []
    for raw in jd_text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        # Heading: "## How to apply" -> "How to apply"
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        # Bullets: "- item" / "* item" / "+ item" -> "• item"
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)
        # Emphasis: **bold**, *italic*, __x__, _x_ -> plain
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", line)
        line = re.sub(r"__(.+?)__", r"\1", line)
        # Markdown links [text](url) -> "text (url)"
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)
        # Inline code `x` -> x
        line = line.replace("`", "")
        out_lines.append(line)
    text = "\n".join(out_lines)
    # Collapse 3+ blank lines to a single blank line.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
