"""Always-on agent context (SOUL / USER / operating notes).

Nexus has two distinct kinds of "memory", and they are handled differently:

  - RETRIEVED memory — durable, growing facts written by `append_memory` to
    `reference/memory.md` and pulled on demand via `search_reference`. Scales with the
    vault; only the relevant slice enters a given turn.
  - ALWAYS-ON context — small, stable, high-value text that must be in EVERY turn: the
    agent's persona (SOUL), what's known about the owner (USER), house operating rules.
    These live as plain markdown in `vault/context/` and are injected verbatim into the
    system prompt by `run_loop`.

`vault/context/` is a NON_NOTE_DIR (see vault/io.py): excluded from search, retrieval, and
INDEX.md — it is prompt material, not a queryable note. Keep it SMALL (it is paid on every
call and prompt-cached); push anything large or growing into a reference note instead.
"""

from __future__ import annotations

from nexus.vault import io


def context_dir():
    return io.vault_root() / "context"


def load_context() -> str:
    """Concatenate every `vault/context/*.md` file (sorted) into one prompt block.

    Returns "" when the folder is absent or empty, so the loop degrades cleanly.
    """
    directory = context_dir()
    if not directory.exists():
        return ""
    parts: list[str] = []
    for path in sorted(directory.glob("*.md")):
        if path.name == io.INDEX_FILENAME:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)
