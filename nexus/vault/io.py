"""Frontmatter parse + the write gate (spec §3.2 / §3.5).

`write_note` is THE GATE: the only path to disk for machine writes. Every write is
validated against the schema (so `extra="forbid"` rejects typos loudly) and frontmatter
keys are emitted in model-declaration order. `parse_note` re-validates on read so a hand
edit in Obsidian — the one writer the gate can't intercept — is still caught.

Shared vault helpers (folder mapping, walking, slugs) also live here so the search,
index, queries, and write layers all read the vault the same way.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import frontmatter
import yaml
from pydantic import TypeAdapter

from nexus.config import settings
from nexus.vault.schema import AnyNote, CoreNote, Family

_ADAPTER: TypeAdapter = TypeAdapter(AnyNote)

INDEX_FILENAME = "INDEX.md"

# Top-level folders that hold NON-note material and must stay out of the corpus:
#   system/   — archived attachments (§3.6) and per-connector sync state
#   context/  — always-on agent context (SOUL/USER), injected verbatim, never retrieved
# Excluded from search, retrieval, and INDEX.md generation.
NON_NOTE_DIRS: frozenset[str] = frozenset({"system", "context"})

# Family -> top-level folder (spec §8: organized by family at the top level).
_FAMILY_DIR: dict[Family, str] = {
    Family.reference: "reference",
    Family.entity: "entity",
    Family.event: "events",
    Family.task: "tasks",
}


def vault_root() -> Path:
    return settings.vault_path


def family_dir(family: Family) -> Path:
    return vault_root() / _FAMILY_DIR[family]


def slugify(text: str) -> str:
    """Stable, filesystem-safe slug for note filenames."""
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug or "untitled"


def resolve_note_path(path: str | Path) -> Path | None:
    """Resolve a caller-supplied path to a real note file inside the vault, or None.

    Accepts paths as emitted by the vault walk (relative to CWD when vault_path is
    relative), absolute paths, and vault-root-relative paths. INDEX.md, anything in
    NON_NOTE_DIRS, and paths escaping the vault all resolve to None — this is the
    boundary check for every tool that takes a note path from the model.
    """
    root = vault_root().resolve()
    given = Path(path)
    candidates = [given] if given.is_absolute() else [given, root / given]
    target = next((c.resolve() for c in candidates if c.is_file()), None)
    if target is None or target.name == INDEX_FILENAME:
        return None
    try:
        rel = target.relative_to(root)
    except ValueError:
        return None  # escapes the vault
    if rel.parts and rel.parts[0] in NON_NOTE_DIRS:
        return None  # attachments/context are not queryable notes
    return target


def parse_note(path: Path) -> CoreNote:
    """Load a note, re-validating its frontmatter against the schema (§3.2 #3)."""
    note, _ = read_note(path)
    return note


def read_note(path: Path) -> tuple[CoreNote, str]:
    """Load a note's validated frontmatter AND its markdown body."""
    post = frontmatter.load(path)
    note = _ADAPTER.validate_python(dict(post.metadata))
    return note, post.content


def iter_notes(root: Path | None = None) -> Iterator[tuple[Path, CoreNote, str]]:
    """Walk every note under `root` (default: whole vault), excluding INDEX.md.

    Invalid notes (e.g. a bad hand edit) are skipped rather than aborting the walk; the
    re-validation in read_note is what surfaces them when a single note is opened.
    """
    root = root or vault_root()
    if not root.exists():
        return
    for path in sorted(root.rglob("*.md")):
        if path.name == INDEX_FILENAME:
            continue
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in NON_NOTE_DIRS:
            continue  # skip attachments and always-on context
        try:
            note, body = read_note(path)
        except Exception:  # noqa: BLE001 — a malformed note must not break the corpus walk
            continue
        yield path, note, body


def _serialize_frontmatter(note: CoreNote) -> str:
    """Emit YAML frontmatter in model-declaration order (§3.2: order == key order).

    model_dump(mode="json") yields YAML-safe primitives (enums -> values, dates ->
    strings) and preserves declaration order; we drop None/empty for clean notes.
    """
    dumped = note.model_dump(mode="json")
    ordered = {k: v for k, v in dumped.items() if v is not None and v != []}
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True).strip()


def write_note(note: CoreNote, path: Path, body: str = "") -> Path:
    """THE GATE. Validate then write; returns the path. Raises on schema violation.

    NOTE: callers go through `nexus/writes.py`, never this directly, so every machine
    write also routes through the trust-aware write surface.

    Every successful write marks BOTH indexes dirty (search corpus + INDEX.md files):
    because the gate is the only path to disk, no write path — loop, sync, stream,
    workflow, MCP tool — can forget. Rebuilds happen lazily (search: on next query;
    INDEX.md: at the next batch boundary via index.regenerate_if_dirty), so a burst of
    writes coalesces into one rebuild.
    """
    # Round-trip through the adapter to enforce the discriminated union + extra="forbid".
    _ADAPTER.validate_python(note.model_dump(mode="json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"---\n{_serialize_frontmatter(note)}\n---\n\n{body}".rstrip() + "\n"
    path.write_text(content, encoding="utf-8")

    from nexus.vault import index, search  # local import — io must stay dependency-light

    search.mark_dirty()
    index.mark_dirty()
    return path
