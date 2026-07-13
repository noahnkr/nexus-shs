"""Generated indexes.

Each folder carries an auto-generated INDEX.md (uppercase: sorts to top, signals
"generated meta"). STALE INDEXES ARE WORSE THAN NONE, so they are ALWAYS regenerated from
frontmatter, never hand-edited, and excluded from search.

Three render shapes by folder type:
  - Leaf (folder of notes)       -> a table, one row per note projected from frontmatter.
  - Branch (folder of subfolders)-> a roll-up: child indexes + counts + one line each.
  - Calendar (events & tasks)    -> chronological. Events newest-first; the task queue
    renders "No open tasks." when empty (an empty queue is a SIGNAL, not a blank table).

The file is the cold/human view (Obsidian). The hot path is the live tool
get_vault_map / list_folder so agents never parse stale markdown.
"""

from __future__ import annotations

from pathlib import Path

from nexus.vault import io
from nexus.vault.schema import CoreNote, Status

INDEX_FILENAME = "INDEX.md"
NO_OPEN_TASKS = "No open tasks."

_CALENDAR_FOLDERS = {"events", "tasks"}


def _folder_notes(folder: Path) -> list[tuple[Path, CoreNote]]:
    """Validated notes directly in `folder` (non-recursive, INDEX.md excluded)."""
    out: list[tuple[Path, CoreNote]] = []
    for path in sorted(folder.glob("*.md")):
        if path.name == INDEX_FILENAME:
            continue
        try:
            note, _ = io.read_note(path)
        except Exception:  # noqa: BLE001 — skip malformed notes, don't abort the index
            continue
        out.append((path, note))
    return out


def _esc(text: str | None) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def render_leaf(folder: Path) -> str:
    """Table: one row per note (title / summary / status / tags / updated)."""
    rows = sorted(_folder_notes(folder), key=lambda pn: pn[1].updated, reverse=True)
    lines = [f"# {folder.name}", "", f"{len(rows)} note(s). Generated — do not edit.", ""]
    lines += ["| Title | Summary | Status | Tags | Updated |", "|---|---|---|---|---|"]
    for path, note in rows:
        link = f"[{_esc(note.title)}]({path.name})"
        tags = ", ".join(note.tags)
        lines.append(
            f"| {link} | {_esc(note.summary)} | {note.status} | {_esc(tags)} | {note.updated} |"
        )
    return "\n".join(lines) + "\n"


def render_branch(folder: Path) -> str:
    """Roll-up of child folders: link + count + one summary line each (not a flat dump)."""
    children = sorted(
        p for p in folder.iterdir() if p.is_dir() and p.name not in io.NON_NOTE_DIRS
    )
    lines = [f"# {folder.name}", "", "Generated roll-up — do not edit.", ""]
    for child in children:
        count = sum(1 for p in child.rglob("*.md") if p.name != INDEX_FILENAME)
        lines.append(f"- **[{child.name}]({child.name}/{INDEX_FILENAME})** — {count} note(s)")
    return "\n".join(lines) + "\n"


def render_calendar(folder: Path, *, is_tasks: bool = False) -> str:
    """Chronological. Events newest-first; tasks emit NO_OPEN_TASKS when empty."""
    notes = _folder_notes(folder)
    lines = [f"# {folder.name}", "", "Generated — do not edit.", ""]

    if is_tasks:
        open_tasks = [
            (p, n) for p, n in notes if str(n.status) == str(Status.open)
        ]
        if not open_tasks:
            lines.append(NO_OPEN_TASKS)
            return "\n".join(lines) + "\n"
        for path, note in sorted(open_tasks, key=lambda pn: pn[1].created, reverse=True):
            action = getattr(note, "action", None) or note.title
            chan = getattr(note, "channel", None)
            suffix = f" → {chan}" if chan else ""
            lines.append(f"- [ ] [{_esc(action)}]({path.name}){suffix} ({note.created})")
        return "\n".join(lines) + "\n"

    for path, note in sorted(notes, key=lambda pn: pn[1].created, reverse=True):
        lines.append(f"- **{note.created}** — [{_esc(note.title)}]({path.name})")
    if len(lines) == 4:
        lines.append("_No events recorded._")
    return "\n".join(lines) + "\n"


def regenerate(folder: Path) -> Path:
    """Pick the render shape by folder type and (over)write its INDEX.md.

    Always rebuilds from frontmatter — never reads the previous INDEX.md.
    """
    folder.mkdir(parents=True, exist_ok=True)
    if folder.name in _CALENDAR_FOLDERS:
        content = render_calendar(folder, is_tasks=(folder.name == "tasks"))
    elif any(p.is_dir() for p in folder.iterdir()):
        content = render_branch(folder)
    else:
        content = render_leaf(folder)
    out = folder / INDEX_FILENAME
    out.write_text(content, encoding="utf-8")
    return out


def regenerate_all(root: Path | None = None) -> list[Path]:
    """Regenerate INDEX.md for the vault root and every note-bearing subfolder.

    Skips non-note folders (system/ attachments, context/) so no INDEX.md is generated
    inside them (io.NON_NOTE_DIRS).
    """
    global _dirty
    root = root or io.vault_root()
    written: list[Path] = [regenerate(root)]
    for sub in sorted(p for p in root.rglob("*") if p.is_dir()):
        if sub.relative_to(root).parts[0] in io.NON_NOTE_DIRS:
            continue
        written.append(regenerate(sub))
    _dirty = False
    return written


_dirty: bool = False


def mark_dirty() -> None:
    """Flag the INDEX.md files as stale. Called by the write gate (io.write_note) on
    every successful write; batch boundaries call regenerate_if_dirty() to coalesce."""
    global _dirty
    _dirty = True


def regenerate_if_dirty() -> list[Path]:
    """Regenerate INDEX.md files iff a write dirtied them since the last regeneration.

    THE batch boundary primitive: cheap no-op when clean, so every path that *might*
    have written (agent loop end, cron job end, stream event, workflow run end, MCP
    write tool) calls it unconditionally. Stale indexes are worse than none;
    scattered per-write regeneration is worse than one per batch.
    """
    return regenerate_all() if _dirty else []
