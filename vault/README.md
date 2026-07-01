# The vault

This folder is the **knowledge layer** — a directory of markdown notes with YAML
frontmatter that is the persistent, compounding artifact of the whole system. It is a git
repo and an [Obsidian](https://obsidian.md) vault at the same time: agents write it, humans
read and curate it.

In the repo, `vault/` is the **seed** baked into the image. In production the live vault
lives on a mounted volume, seeded once from this folder and never clobbered after (see
[`../docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md)).

## The four note families + two non-note folders

| Folder | Family | Who writes it | What it holds |
|---|---|---|---|
| `reference/` | reference | humans (via ingest) | authored, slow knowledge: SOPs, policy, pricing, voice |
| `entity/` | entity | machine | current distilled state of each tracked thing (one note each) |
| `events/` | event | machine | append-only audit log, one note per day |
| `tasks/` | task | machine | the human-approval queue (one note per open item) |
| `context/` | — (not a note) | humans | always-on agent context (SOUL/USER); injected verbatim, never searched |
| `system/` | — (not a note) | machine | archived attachments + connector sync state; never searched |

Every note's shape is enforced by `nexus/vault/schema.py`. `INDEX.md` in each folder is
**generated** from frontmatter — never edit it by hand.

`context/` and `system/` are excluded from search, retrieval, and index generation
(`io.NON_NOTE_DIRS`), because they are prompt material and archives, not queryable notes.
