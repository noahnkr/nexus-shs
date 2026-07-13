"""KNOWLEDGE LAYER — the vault.

A directory of markdown notes with YAML frontmatter, split into REFERENCE (authored) and
STATE (entity / event / task). The schema is the contract; the write gate is the only
path to disk for machine writes; retrieval is hybrid and in-process.
"""
