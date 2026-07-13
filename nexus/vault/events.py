"""Append-only event log.

The permanent, undeletable audit trail. One note per day under `events/`. Entity state is
a *projection* of this log (event sourcing). "Log always": every stimulus is
appended regardless of outcome; this module is the mechanism.

Note: the trust-aware entry point is `nexus.writes.append_log`; this holds the raw
day-note mechanics so the write surface stays thin.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from nexus.vault import io
from nexus.vault.schema import EventNote, Status


def events_dir() -> Path:
    return io.vault_root() / "events"


def day_note_path(day: date | None = None) -> Path:
    day = day or datetime.now(UTC).date()
    return events_dir() / f"{day.isoformat()}.md"


def append_entry(summary: str, day: date | None = None) -> Path:
    """Append one chronological entry to today's event note (create it if absent).

    Append-only: prior entries are never rewritten — new entries are added with a UTC
    timestamp and the note is persisted through the gate (`nexus.vault.io.write_note`).
    """
    now = datetime.now(UTC)
    day = day or now.date()
    path = day_note_path(day)

    if path.exists():
        note, body = io.read_note(path)
        if not isinstance(note, EventNote):  # defensive: a mistyped day note
            note = _new_day_note(day)
            body = ""
    else:
        note = _new_day_note(day)
        body = ""

    stamp = now.strftime("%H:%M:%SZ")
    note.entries.append(f"{stamp} — {summary}")
    note.updated = day
    return io.write_note(note, path, body)


def _new_day_note(day: date) -> EventNote:
    return EventNote(
        title=f"Events {day.isoformat()}",
        status=Status.published,
        created=day,
        updated=day,
        entries=[],
    )
