"""Text extraction (spec §3.7, stage 1 of ingest).

Turn a raw source into text the classifier can read. Lossless originals: file-based ingest
archives the binary original (the pipeline does this); text-only ingest treats the note
body as authoritative and archives nothing (§3.6).

Text formats are handled here with no extra dependencies. For binary formats (pdf/docx),
a fork registers an extractor — the dispatch point is `extract_text`.
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
    raise NotImplementedError(
        f"§3.7 — no extractor registered for '{suffix}'. Add one here (e.g. pdf/docx) for "
        "binary formats; text formats are handled natively."
    )
