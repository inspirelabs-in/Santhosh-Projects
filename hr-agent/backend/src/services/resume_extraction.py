"""Resume text extraction: PyMuPDF → Tesseract OCR fallback → python-docx.

Called by the parse_resume activity. The raw file is fetched from R2 first
(in the activity); this module takes bytes + filename and returns plain text
plus the method used (for audit + confidence downgrades).
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

ExtractionMethod = Literal["native_pdf", "ocr_pdf", "docx", "unsupported"]

# Below this text length (after native PDF parse) we assume the PDF is a
# scanned image and fall through to OCR.
_NATIVE_TEXT_MIN_CHARS = 50


@dataclass
class ExtractionResult:
    text: str
    method: ExtractionMethod
    page_count: int
    char_count: int


class UnsupportedFormatError(RuntimeError):
    """Raised for file types the parser cannot handle (e.g. .rar, image-only)."""


def _extract_pdf_sync(content: bytes) -> ExtractionResult:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=content, filetype="pdf")
    try:
        native_text_parts: list[str] = []
        for page in doc:
            native_text_parts.append(page.get_text())
        native_text = "\n".join(native_text_parts).strip()

        if len(native_text) >= _NATIVE_TEXT_MIN_CHARS:
            return ExtractionResult(
                text=native_text,
                method="native_pdf",
                page_count=doc.page_count,
                char_count=len(native_text),
            )

        # OCR fallback -- scanned PDF / image-only pages.
        logger.info("PDF has %d native chars; falling through to OCR", len(native_text))
        try:
            import pytesseract
            from PIL import Image
        except ImportError as e:
            raise UnsupportedFormatError(f"OCR dependencies missing: {e}") from e

        ocr_parts: list[str] = []
        for page in doc:
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            ocr_parts.append(pytesseract.image_to_string(img))
        ocr_text = "\n".join(ocr_parts).strip()
        return ExtractionResult(
            text=ocr_text,
            method="ocr_pdf",
            page_count=doc.page_count,
            char_count=len(ocr_text),
        )
    finally:
        doc.close()


def _extract_docx_sync(content: bytes) -> ExtractionResult:
    from docx import Document

    doc = Document(io.BytesIO(content))
    parts: list[str] = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    text = "\n".join(parts).strip()
    return ExtractionResult(text=text, method="docx", page_count=1, char_count=len(text))


async def extract_resume_text(*, content: bytes, filename: str) -> ExtractionResult:
    """Dispatch on filename suffix. Runs blocking IO/CPU work on a thread."""
    name = filename.lower().strip()

    if name.endswith(".pdf"):
        return await asyncio.to_thread(_extract_pdf_sync, content)
    if name.endswith(".docx"):
        return await asyncio.to_thread(_extract_docx_sync, content)
    if name.endswith(".doc"):
        raise UnsupportedFormatError(
            "Legacy .doc format not supported. Ask the candidate to resend as .docx or .pdf."
        )
    if name.endswith((".rar", ".zip", ".7z")):
        raise UnsupportedFormatError(f"Archive format {name.rsplit('.', 1)[-1]} not supported")
    if name.endswith((".jpg", ".jpeg", ".png", ".tiff", ".bmp")):
        # Single-image resume -- rare but we'll OCR it.
        try:
            import pytesseract
            from PIL import Image
        except ImportError as e:
            raise UnsupportedFormatError(f"OCR dependencies missing: {e}") from e

        def _ocr_image() -> ExtractionResult:
            img = Image.open(io.BytesIO(content))
            text = pytesseract.image_to_string(img).strip()
            return ExtractionResult(text=text, method="ocr_pdf", page_count=1, char_count=len(text))

        return await asyncio.to_thread(_ocr_image)

    raise UnsupportedFormatError(f"Cannot parse: {filename}")
