"""Render an ``AssignmentBriefOut`` into a multi-page PDF.

Produces a polished, branded document that mimics the GrabOn Vibe Coder
Challenge layout: cover page, context / strategic framing, one page (or
more) per problem with structured sections, evaluation rubric, and
submission requirements.

Falls back gracefully when v2 enrichment fields are absent (older briefs
just render the markdown brief_md and the bare problem list).
"""

from __future__ import annotations

import io
import logging
from typing import Any, Mapping

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)


# Brand palette - kept locally so we don't depend on a global theme module.
_BRAND_GREEN = colors.HexColor("#1F8A3A")
_BRAND_DARK = colors.HexColor("#0F2027")
_INK = colors.HexColor("#1B1F23")
_MUTED = colors.HexColor("#586069")
_RULE = colors.HexColor("#E1E4E8")
_CHIP_BG = colors.HexColor("#EEF6F0")


def _styles() -> Mapping[str, ParagraphStyle]:
    s = getSampleStyleSheet()
    out = {
        "h_cover": ParagraphStyle(
            "h_cover", parent=s["Heading1"], fontName="Helvetica-Bold",
            fontSize=26, leading=30, textColor=_BRAND_DARK, spaceAfter=6,
        ),
        "h_sub": ParagraphStyle(
            "h_sub", parent=s["Heading2"], fontName="Helvetica-Bold",
            fontSize=14, leading=18, textColor=_BRAND_GREEN, spaceAfter=8,
        ),
        "h1": ParagraphStyle(
            "h1", parent=s["Heading1"], fontName="Helvetica-Bold",
            fontSize=20, leading=24, textColor=_BRAND_DARK,
            spaceBefore=4, spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2", parent=s["Heading2"], fontName="Helvetica-Bold",
            fontSize=13, leading=16, textColor=_BRAND_GREEN,
            spaceBefore=10, spaceAfter=6,
        ),
        "h3": ParagraphStyle(
            "h3", parent=s["Heading3"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, textColor=_BRAND_DARK,
            spaceBefore=8, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body", parent=s["BodyText"], fontName="Helvetica",
            fontSize=10, leading=14, textColor=_INK, spaceAfter=6,
            alignment=0,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=s["BodyText"], fontName="Helvetica",
            fontSize=10, leading=14, textColor=_INK,
            leftIndent=12, bulletIndent=2, spaceAfter=3,
        ),
        "muted_small": ParagraphStyle(
            "muted_small", parent=s["BodyText"], fontName="Helvetica",
            fontSize=8.5, leading=12, textColor=_MUTED, spaceAfter=4,
        ),
        "tag": ParagraphStyle(
            "tag", parent=s["BodyText"], fontName="Helvetica-Bold",
            fontSize=8, leading=10, textColor=_BRAND_GREEN, spaceAfter=2,
        ),
        "label": ParagraphStyle(
            "label", parent=s["BodyText"], fontName="Helvetica-Bold",
            fontSize=8, leading=10, textColor=_MUTED, spaceAfter=2,
        ),
    }
    return out


def _para(text: str, style: ParagraphStyle) -> Paragraph:
    if not text:
        text = "&nbsp;"
    # Escape angle brackets minimally so user content can't break the parser.
    safe = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    # Allow simple inline bold via **...**
    import re as _re

    safe = _re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", safe)
    return Paragraph(safe, style)


def _rule(color=_RULE, thickness: float = 0.6, width: str | float = "100%") -> HRFlowable:
    return HRFlowable(width=width, thickness=thickness, color=color, spaceBefore=4, spaceAfter=4)


def _chip_row(items: list[str], st: Mapping[str, ParagraphStyle]) -> Table | None:
    items = [i for i in (items or []) if i]
    if not items:
        return None
    cells = []
    row = []
    for it in items[:10]:
        row.append(_para(f"[ {it} ]", st["tag"]))
    cells.append(row)
    n = len(row) or 1
    col_w = (180 / n) * mm
    t = Table(cells, colWidths=[col_w] * n, hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _CHIP_BG),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("BOX", (0, 0), (-1, -1), 0.4, _RULE),
            ]
        )
    )
    return t


def _meta_table(rows: list[tuple[str, str]], st: Mapping[str, ParagraphStyle]) -> Table:
    data = []
    for label, value in rows:
        data.append([_para(label, st["label"]), _para(value or "—", st["body"])])
    t = Table(data, colWidths=[36 * mm, 140 * mm], hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -2), 0.3, _RULE),
            ]
        )
    )
    return t


def _on_page(canvas, doc):
    """Footer + subtle header on every page."""
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(_MUTED)
    company = getattr(doc, "_company", "")
    title = getattr(doc, "_title", "")
    if title:
        canvas.drawString(20 * mm, 287 * mm, title)
    canvas.drawRightString(190 * mm, 287 * mm, "CONFIDENTIAL")
    canvas.setStrokeColor(_RULE)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 285 * mm, 190 * mm, 285 * mm)
    canvas.drawString(20 * mm, 10 * mm, f"{company} | Take-home Assignment")
    canvas.drawRightString(190 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def render_assignment_pdf(
    brief: Mapping[str, Any],
    *,
    company_name: str,
    role_title: str,
) -> bytes:
    """Render a brief dict (AssignmentBriefOut.model_dump()) to a PDF blob."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=24 * mm,
        bottomMargin=18 * mm,
        title=f"{company_name} | {role_title} | Take-home Assignment",
        author=company_name,
    )
    doc._company = company_name
    doc._title = brief.get("cover_title") or f"{company_name} Challenge"

    st = _styles()
    story: list[Any] = []

    # ---- Cover ----
    cover_title = brief.get("cover_title") or f"{company_name} | {role_title} Challenge"
    story.append(_para(cover_title, st["h_cover"]))
    confidential = brief.get("confidential_tag") or "CONFIDENTIAL"
    story.append(_para(confidential, st["muted_small"]))
    story.append(Spacer(1, 8))
    story.append(_para(f"Take-home assignment for the {role_title} role.", st["h_sub"]))
    story.append(_rule(color=_BRAND_GREEN, thickness=1.2))
    story.append(Spacer(1, 6))

    problems = brief.get("problems") or []
    subm = brief.get("submission_format") or {}
    rows = [
        ("Format", f"{len(problems)} independent project briefs - candidate chooses one"),
        ("Difficulty", "Hard. Each project takes 2.5 to 3 days of focused work."),
        ("Submission", subm.get("type") or "GitHub repo + README + Loom walkthrough"),
        ("Deadline", f"{subm.get('deadline_days') or 7} days from receipt"),
    ]
    story.append(_meta_table(rows, st))
    story.append(Spacer(1, 10))

    if brief.get("company_context"):
        story.append(_para("About Us", st["h2"]))
        for para in str(brief["company_context"]).split("\n\n"):
            story.append(_para(para, st["body"]))
    if brief.get("new_initiatives"):
        story.append(_para("What We Are Building Next", st["h2"]))
        for para in str(brief["new_initiatives"]).split("\n\n"):
            story.append(_para(para, st["body"]))
    if brief.get("strategic_context"):
        story.append(_para("Strategic Context", st["h2"]))
        story.append(_para(str(brief["strategic_context"]), st["body"]))

    what_we_look_for = brief.get("what_we_look_for") or []
    if what_we_look_for:
        story.append(_para("What We Look For In Builders", st["h2"]))
        for b in what_we_look_for:
            story.append(_para(f"&bull; {b}", st["bullet"]))

    story.append(PageBreak())

    # ---- Problems ----
    for idx, p in enumerate(problems, start=1):
        chunk: list[Any] = []
        title = p.get("title") or f"Problem {idx}"
        chunk.append(_para(f"Project {idx:02d}: {title}", st["h1"]))
        if p.get("vertical"):
            chunk.append(_para(p["vertical"], st["muted_small"]))
        chip = _chip_row(p.get("tags") or [], st)
        if chip is not None:
            chunk.append(chip)
            chunk.append(Spacer(1, 4))
        meta_rows = []
        if p.get("difficulty"):
            meta_rows.append(("Difficulty", p["difficulty"]))
        if p.get("estimated_minutes"):
            meta_rows.append(("Effort", f"~{int(p['estimated_minutes'] / 60)} hours focused"))
        if p.get("tied_to_jd"):
            meta_rows.append(("Probes", p["tied_to_jd"]))
        if meta_rows:
            chunk.append(_meta_table(meta_rows, st))
            chunk.append(Spacer(1, 6))
        if p.get("challenge"):
            chunk.append(_para("The Challenge", st["h3"]))
            chunk.append(_para(p["challenge"], st["body"]))
        elif p.get("statement"):
            chunk.append(_para("Problem Statement", st["h3"]))
            chunk.append(_para(p["statement"], st["body"]))
        if p.get("why_it_matters"):
            chunk.append(_para("Why This Matters", st["h3"]))
            chunk.append(_para(p["why_it_matters"], st["body"]))
        techs = p.get("technical_requirements") or []
        if techs:
            chunk.append(_para("Technical Requirements", st["h3"]))
            for b in techs:
                chunk.append(_para(f"&bull; {b}", st["bullet"]))
        if p.get("what_to_submit"):
            chunk.append(_para("What To Submit", st["h3"]))
            chunk.append(_para(p["what_to_submit"], st["body"]))
        artifacts = p.get("expected_artifacts") or []
        if artifacts:
            chunk.append(_para("Expected Artifacts", st["h3"]))
            chunk.append(_para(", ".join(f"<b>{a}</b>" for a in artifacts), st["body"]))
        evals = p.get("evaluation_bullets") or []
        if evals:
            chunk.append(_para("How It Will Be Evaluated", st["h3"]))
            for b in evals:
                chunk.append(_para(f"&bull; {b}", st["bullet"]))
        story.extend(chunk)
        story.append(PageBreak())

    # ---- Rubric ----
    rubric = brief.get("evaluation_rubric") or {}
    crits = (rubric.get("criteria") if isinstance(rubric, dict) else None) or []
    if crits:
        story.append(_para("Evaluation Rubric", st["h1"]))
        story.append(_para("Equal weight across all dimensions. Score 1-5 per dimension.", st["body"]))
        data = [[_para("Dimension", st["label"]), _para("What We Look For", st["label"]), _para("Weight", st["label"])]]
        for c in crits:
            data.append([
                _para(c.get("name") or "", st["body"]),
                _para(c.get("description") or "", st["body"]),
                _para(f"{c.get('weight') or 0}%", st["body"]),
            ])
        t = Table(data, colWidths=[40 * mm, 110 * mm, 26 * mm], hAlign="LEFT", repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _CHIP_BG),
                    ("BOX", (0, 0), (-1, -1), 0.4, _RULE),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, _RULE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(t)
        story.append(Spacer(1, 10))

    # ---- Submission requirements ----
    subs = brief.get("submission_requirements") or []
    if subs:
        story.append(_para("Submission Requirements", st["h1"]))
        for b in subs:
            story.append(_para(f"&bull; {b}", st["bullet"]))
        story.append(Spacer(1, 6))

    if subm.get("instructions"):
        story.append(_para("How To Submit", st["h2"]))
        story.append(_para(subm["instructions"], st["body"]))

    story.append(Spacer(1, 14))
    story.append(_rule(color=_BRAND_GREEN, thickness=1.2))
    story.append(_para(
        "Good luck. We are looking for builders who make us say: "
        "'We should hire this person immediately.' That is the bar.",
        st["h3"],
    ))

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()
