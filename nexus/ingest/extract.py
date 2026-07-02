"""Text extraction (spec §3.7, stage 1 of ingest).

Turn a raw source into text the classifier can read. Lossless originals: file-based ingest
archives the binary original (the pipeline does this); text-only ingest treats the note
body as authoritative and archives nothing (§3.6).

Text formats are handled here with no extra dependencies. Binary formats dispatch to a
per-suffix extractor (all in-process, pure-Python — no external service): `.pdf` via pypdf,
`.docx` via python-docx. The dispatch point is `extract_text`.

Google Docs are NOT handled here: they are not a byte format but a Drive resource. Export
them to text/markdown (or .docx) in the Drive connector's fetch step, so they arrive as
text or as a .docx the extractor below already reads.
"""

from __future__ import annotations

import re
from pathlib import Path

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".log"}
_TAG = re.compile(r"<[^>]+>")


def extract_text(source: Path) -> str:
    """Return plain text for `source`, dispatching on suffix."""
    suffix = source.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        return source.read_text(encoding="utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        return _TAG.sub(" ", source.read_text(encoding="utf-8", errors="replace"))
    if suffix == ".pdf":
        return _extract_pdf(source)
    if suffix == ".docx":
        return _extract_docx(source)
    raise NotImplementedError(
        f"§3.7 — no extractor registered for '{suffix}'. Add one here for binary formats; "
        "text formats are handled natively."
    )


def _extract_pdf(source: Path) -> str:
    """Extract the embedded text layer of a digital PDF via pypdf.

    Image-only (scanned) PDFs have no text layer and yield an empty result; we raise so a
    human notices rather than silently drafting an empty note. OCR (Tesseract) is a separate
    concern — add it here only if scanned docs are in scope.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(source))
    text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if not text:
        raise ValueError(
            f"§3.7 — '{source.name}' yielded no extractable text (likely a scanned/image "
            "PDF). OCR is not wired in; extract text upstream or add an OCR extractor."
        )
    return text


def _extract_docx(source: Path) -> str:
    """Extract paragraph and table text from a .docx via python-docx."""
    from docx import Document

    doc = Document(str(source))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(parts).strip()
