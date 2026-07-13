"""Bulk bootstrap via the Batches API (spec §3.7).

A cheaper variant of the pipeline for seeding a backlog. The downstream assembly,
write-gate, and reindex are identical to pipeline.py; only the classification step is
batched.

Current implementation runs the pipeline per file and reindexes once at the end — correct
and idempotent. Swapping the per-file `classify` calls for a single Anthropic Batches-API
submission is a drop-in optimization (same `emit_note` tool schema, one job over all
extracted texts) and the one place worth the extra plumbing when the backlog is large.
"""

from __future__ import annotations

from pathlib import Path

from nexus.ingest.extract import extract_text
from nexus.ingest.pipeline import assemble, ingest_file
from nexus.vault.schema import Family

__all__ = ["ingest_batch", "extract_text", "assemble"]


def ingest_batch(
    sources: list[Path],
    *,
    family: Family = Family.reference,
    subfolder: str | None = None,
    overrides: dict | None = None,
) -> list[Path]:
    """Ingest many sources; indexes settle once at the end (§3.7)."""
    paths: list[Path] = []
    for src in sources:
        try:
            paths.append(ingest_file(src, family=family, subfolder=subfolder, overrides=overrides))
        except NotImplementedError:
            continue  # unsupported format — skip, keep the batch going
    _settle_once()
    return paths


def _settle_once() -> None:
    # Per-file ingest already settles; this is belt-and-braces for the batched-classify
    # future where per-file settling goes away. No-op when clean.
    from nexus.vault.index import regenerate_if_dirty

    regenerate_if_dirty()
