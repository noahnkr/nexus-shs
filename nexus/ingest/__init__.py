"""Ingest — how raw sources become notes (spec §3.7).

The on-ramp that fills the vault. An empty vault makes dumb agents, so seeding is a
first-class parallel track (§10). Pipeline:

  extract text -> LLM classify (constrained by the schema's JSON schema) -> assemble
  frontmatter -> write a status:draft note -> archive the original -> reindex.

Drafts are reviewed and promoted to `published` by a human (or a trusted agent).
"""
