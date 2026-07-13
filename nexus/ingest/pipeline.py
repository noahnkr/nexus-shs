"""The ingest pipeline (spec §3.7).

extract text -> LLM classify -> assemble frontmatter -> write status:draft note ->
archive original (to system/attachments/) -> reindex.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
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
    data["created"] = _coerce_date(data.get("created"), fallback=today)
    data["updated"] = today
    if source_ref:
        data["source_ref"] = source_ref
    return model_for(family).model_validate(data)


def _coerce_date(value: Any, *, fallback: str) -> str:
    """A usable ISO date from whatever the classifier emitted, else `fallback` (today).

    Undated documents make the model emit null/""/invalid dates; a missing document date
    must degrade to ingestion date, never fail the ingest or write a null `created`.
    """
    if not value:
        return fallback
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return fallback


def ingest_file(
    source: Path,
    *,
    family: Family = Family.reference,
    subfolder: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> Path:
    """Run the full pipeline for one source file; return the draft note path (§3.7).

    `subfolder` optionally organizes reference notes into a human-browsable subtree
    (e.g. reference/hr/…). It is purely for curation/Obsidian navigation: retrieval walks
    all subfolders and filtering still uses frontmatter, so a subfolder is never required.
    `overrides` pins frontmatter the curator already knows (e.g. category/audience) over
    whatever the classifier emits — the schema still validates the result.
    Drafts are promoted to `published` by a human (or a trusted agent).
    """
    text = extract_text(source)
    frontmatter = classify(text, hint_family=family)
    if overrides:
        frontmatter.update(overrides)
    source_ref = f"file:{family.value}:{source.name}"
    note = assemble(frontmatter, family=family, source_ref=source_ref)

    # The extracted text IS the note body for every format: search/retrieval index bodies,
    # so a bodiless note is invisible to answers. The lossless original is archived below
    # and cited via source_ref.
    body = text
    dest_dir = io.family_dir(family)
    if subfolder:
        dest_dir = dest_dir / io.slugify(subfolder)
    path = dest_dir / f"{io.slugify(note.title)}.md"
    io.write_note(note, path, body)

    _archive_original(source)
    _settle_indexes()
    return path


def _archive_original(source: Path) -> None:
    """Keep a lossless copy so the note can always be re-derived (§3.6)."""
    attachments = io.vault_root() / "system" / "attachments"
    attachments.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, attachments / source.name)
    except OSError:
        pass  # best-effort; a missing original must not block the draft


def _settle_indexes() -> None:
    """Batch boundary: the gate marked the indexes dirty; INDEX.md settles here and
    search rebuilds lazily on its next query."""
    from nexus.vault.index import regenerate_if_dirty

    regenerate_if_dirty()
