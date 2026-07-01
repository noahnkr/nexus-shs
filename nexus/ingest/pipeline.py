"""The ingest pipeline (spec §3.7).

extract text -> LLM classify -> assemble frontmatter -> write status:draft note ->
archive original (to system/attachments/) -> reindex.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus.ingest.classify import classify
from nexus.ingest.extract import extract_text
from nexus.vault import io
from nexus.vault.schema import Family, Status, model_for


def assemble(frontmatter: dict[str, Any], *, family: Family, source_ref: str | None) -> Any:
    """Force the authoritative fields and validate into a draft note model."""
    today = datetime.now(UTC).date().isoformat()
    data = dict(frontmatter)
    data.update(family=family.value, status=Status.draft.value)
    data.setdefault("created", today)
    data["updated"] = today
    if source_ref:
        data["source_ref"] = source_ref
    return model_for(family).model_validate(data)


def ingest_file(
    source: Path, *, family: Family = Family.reference, subfolder: str | None = None
) -> Path:
    """Run the full pipeline for one source file; return the draft note path (§3.7).

    `subfolder` optionally organizes reference notes into a human-browsable subtree
    (e.g. reference/hr/…). It is purely for curation/Obsidian navigation: retrieval walks
    all subfolders and filtering still uses frontmatter, so a subfolder is never required.
    Drafts are promoted to `published` by a human (or a trusted agent).
    """
    text = extract_text(source)
    frontmatter = classify(text, hint_family=family)
    source_ref = f"file:{family.value}:{source.name}"
    note = assemble(frontmatter, family=family, source_ref=source_ref)

    body = text if source.suffix.lower() in {".md", ".markdown", ".txt"} else ""
    dest_dir = io.family_dir(family)
    if subfolder:
        dest_dir = dest_dir / io.slugify(subfolder)
    path = dest_dir / f"{io.slugify(note.title)}.md"
    io.write_note(note, path, body)

    _archive_original(source)
    _reindex()
    return path


def _archive_original(source: Path) -> None:
    """Keep a lossless copy so the note can always be re-derived (§3.6)."""
    attachments = io.vault_root() / "system" / "attachments"
    attachments.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, attachments / source.name)
    except OSError:
        pass  # best-effort; a missing original must not block the draft


def _reindex() -> None:
    from nexus.vault.index import regenerate_all
    from nexus.vault.search import reindex

    reindex()
    regenerate_all()
