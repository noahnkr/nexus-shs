# CLAUDE.md — engineering reference for Nexus

Guidance for a coding agent (and humans) working in this repo. Nexus is a **domain-neutral
foundation** meant to be cloned and extended per business. Your job in a fork is to fill
the marked seams and leave the core intact. Read this before changing code.

Companion docs: [`README.md`](README.md) (orientation), [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) (hosting).

> **`§N` in code docstrings** refers to the original framework spec's sections. That spec
> has been consolidated into this file and the README; the mapping is: §1 invariants →
> *Invariants*; §2 stack → *What this system is*; §3 vault → `vault/` + `vault/README.md`;
> §4 connectors, §5 ingress, §6 agents → *Repo map* + *Mechanisms*; §7 forking → *Fork
> checklist*; §8 locked decisions → *Conventions* + *Invariants*. The numbers are kept as
> stable anchors; you don't need the old file.

## Configuring a fork: start with the interview

If this is a **fresh fork** and the owner is present, the intended entry point is the
onboarding interview at [`.claude/commands/onboard.md`](.claude/commands/onboard.md) (run
`/onboard`). It elicits the business and turns it into config: the context files
(`vault/context/SOUL.md`·`USER.md`·`ORG.md`), the entity + reference **schema**
(`vault/schema.py`), the risk tiers (`ingress/rules.py`), a **plan per connector**
(`docs/connectors/<source>.md`, using [`docs/connectors/README.md`](docs/connectors/README.md)),
tailored docs, and a seeding checklist (`docs/SEEDING.md`). The interview *plans* connectors
— it does not implement client/sync code. When you edit `vault/schema.py`, re-run `pytest`.

---

## What this system is (in one screen)

Every input — chat, webhook, cron tick — normalizes to one `Stimulus`, runs one six-stage
loop (`receive → plan → gather → decide → deliver → record`) against a schema-enforced
markdown vault of **reference** (authored) and **state** (entity + event log + task queue),
and produces exactly one of four outputs under one trust rule: *notify owner · vault write
· queue for approval · autonomous act*.

Data flow: `connectors/<source>` (parse) → `connectors/ingress` (auth, normalize, classify
risk, **log always**, ACK <3s, dispatch) → `agents` (the loop) → `vault` (gated writes).
The schema (`vault/schema.py`) is the shared contract beneath all of it.

## Invariants — do not break these

These are load-bearing across every domain. Changing vocabulary/connectors is expected;
changing these is a design smell.

1. **One `Stimulus` envelope** for every entry point (`connectors/ingress/envelope.py`).
   Downstream branches on `source`/`kind` **as data** (lookup tables), never `if` ladders
   over transports.
2. **One six-stage loop** (`agents/loop.py`) for all agents. Agents differ only in trigger,
   prompt, and output channel.
3. **The schema is the single source of truth.** `vault/schema.py` generates the LLM JSON
   schema, templates, and the runtime validator. Add fields only on demonstrated need.
   `extra="forbid"` (typo guard) and *declaration order == frontmatter key order* are
   intentional — preserve both.
4. **The write gate is the only path to disk** for machine writes (`vault/io.py:write_note`,
   reached via `writes.py`). The index re-validates on read to catch hand edits.
5. **The trust boundary is structural, not prompted.** There is deliberately **no tool that
   contacts an outside party.** External-facing actions can only become a `create_task`
   draft. Never add a send/write-external tool to the loop's toolset.
6. **Risk tier is deterministic and authoritative.** `connectors/ingress/rules.py` sets the
   tier via a static table; unknown `(source,kind)` fails safe to `supervised`. The tier is
   passed to the agent as context — the model never decides its own trust level.
7. **Log always; write only change.** Ingress appends every stimulus to the event log
   before acting. Stage 6 writes only if something actually changed.
8. **One process, one volume, in-process search.** No broker, no external vector DB.
   Cron is an HTTP call to `/cron/{job}`.

## Commands

```bash
uv sync                       # or: pip install -e ".[dev]"
uvicorn nexus.app:app --reload
pytest -q                     # acceptance suite (mirrors the build-order exit criteria)
ruff check nexus tests        # lint (must stay clean)
ruff check --fix nexus tests  # autofix
```

The agent-loop test is `skipif` without `ANTHROPIC_API_KEY`; everything else runs keyless.

## Conventions

- **Python 3.12+**, Pydantic v2 + pydantic-settings, FastMCP + Starlette + uvicorn,
  Anthropic Messages API, `python-frontmatter`, `httpx`. Search is pure-Python BM25 +
  `sqlite-vec` + RRF.
- **ruff, line length 100.** Use `datetime.now(UTC)` (not `timezone.utc`), PEP-604 unions
  (`X | None`), sorted imports.
- **Tool logic is plain functions** in `vault/queries.py` (reads) and `writes.py` (writes).
  Those exact functions back **both** the MCP tools (`tools/__init__.py`) and the loop
  (`agents/toolset.py`) — one source of truth, no self-MCP hop. When you add a tool, wire
  both.
- **Middleware is pure-ASGI** (`middleware.py`), *not* `BaseHTTPMiddleware`, so the webhook
  body can be read twice (HMAC + parse). Don't convert it.
- Some pyright warnings on `anthropic` `messages.create(...)` args and pydantic
  `model_validator(mode="after")` are benign SDK/typing-strictness noise; the code is
  correct at runtime (tests prove it). Don't contort code to silence them.

## Repo map (what each file owns)

| Path | Responsibility |
|---|---|
| `nexus/app.py` | build the ASGI app: `/health`, ingress routes, mounted `/mcp` (+ lifespan) |
| `nexus/config.py` | `Settings` (env); `is_prod` fail-fast; `semantic_enabled` |
| `nexus/middleware.py` | pure-ASGI logging + body cap |
| `nexus/writes.py` | `append_log` · `update_entity` · `create_task` · `append_memory` (all gated; **no send**) |
| `nexus/vault/schema.py` | ⚙ families, `Kind`, `Status`, models, `json_schema_for`, `template_for` |
| `nexus/vault/io.py` | `write_note` gate; `read_note`/`iter_notes`/`family_dir`/`slugify`; `NON_NOTE_DIRS` |
| `nexus/vault/index.py` | leaf/branch/calendar `INDEX.md` renderers; `regenerate_all` |
| `nexus/vault/search.py` | `HybridIndex` (BM25 ⊕ dense), `rrf_merge`, `get_index`, `reindex` |
| `nexus/vault/embeddings.py` | `embed()` — Voyage; returns `None` (dormant) without a key |
| `nexus/vault/queries.py` | reads: `search_reference` · `get_entity` · `list_entities` · `search_logs` · `list_open_tasks` |
| `nexus/vault/events.py` | append-only day-note mechanics |
| `nexus/connectors/ingress/envelope.py` | `Stimulus` |
| `nexus/connectors/ingress/security.py` | `verify_hmac_sha256` · `within_window` · `SeenCache` |
| `nexus/connectors/ingress/rules.py` | ⚙ `(source,kind) → tier` table + `classify` |
| `nexus/connectors/ingress/router.py` | `dispatch` → worker (cron→scheduled, else→reactive) |
| `nexus/connectors/ingress/routes.py` | `/webhooks/{source}` (verify→parse→dedup→log→ACK→bg dispatch), `/cron/{job}`; `CONNECTORS` map |
| `nexus/connectors/ingress/jobs.py` | `DETERMINISTIC_JOBS` vs `AGENT_JOBS` cron split |
| `nexus/connectors/example/` | ⚙ sample connector: `webhook.py` · `client.py` · `sync.py` |
| `nexus/ingest/` | `extract` → `classify` (schema-constrained) → `pipeline`/`batch` |
| `nexus/agents/loop.py` | the six-stage engine; `Consequence`; prompt-cache prefix; reindex-once-after |
| `nexus/agents/context.py` | `load_context()` — injects `vault/context/*.md` (SOUL/USER) |
| `nexus/agents/toolset.py` | ⚙ loop tool registry + `anthropic_tool_specs` |
| `nexus/agents/reactive.py` / `scheduled.py` | thin `run_loop` wrappers (lean/job-name prompts, model tiers) |
| `nexus/agents/notify.py` | owner notification (swappable transport; logs by default) |
| `nexus/tools/__init__.py` | `register_all` + `build_mcp` (MCP surface = same plain functions) |

⚙ = a **fork seam** you're expected to edit.

## Mechanisms worth internalizing

- **Write gate.** `writes.py` builds a validated model and calls `io.write_note`, which
  round-trips through the discriminated union (enforcing `extra="forbid"`) and serializes
  frontmatter via `model_dump(mode="json")` in declaration order. Never write vault files
  any other way.
- **Trust gate (structural).** The loop's toolset has read tools + four vault writes.
  External-facing work has *no capability* — it becomes `create_task(action, channel,
  recipient, body)` so the owner approves-and-sends in one step. Keep it that way.
- **Log-always.** `routes.py` appends to the event log inline, before dispatch, regardless
  of tier — so a later crash never loses the event. Dedup is best-effort on top.
- **Reindex once after the loop.** `run_loop` calls `search.reindex()` at the end because
  writes during the loop changed the corpus. The system prompt + tool specs are a stable
  prefix and are prompt-cached.
- **Two memories (don't conflate).** Retrieved memory = `append_memory` → `reference/
  memory.md`, pulled via `search_reference` (scales). Always-on context = small stable
  `vault/context/*.md` (SOUL/USER) injected verbatim by `agents/context.py`. The fixed loop
  rules and per-agent role prompts live in code.
- **NON_NOTE_DIRS.** `vault/context/` and `vault/system/` are excluded from search,
  retrieval, and index generation (`io.NON_NOTE_DIRS`). Attachments live in
  `system/attachments/`; the note's `source_ref` (`"<system>:<family>:<id>"`) is the
  citable backlink returned by the read tools.
- **Entity grouping is frontmatter, not folders.** Entities are flat files keyed by `kind`;
  filter with `list_entities`. `reference/` may use subfolders for human browsing only
  (`ingest_file(..., subfolder=...)`); retrieval walks all subfolders.
- **Obsidian graph.** `[[wikilinks]]` in `related` (and typed relation fields you add)
  render as graph edges and are traversable by the agent.

## Fork checklist

Prefer the **onboarding interview** (`/onboard`, see above) — it walks the owner through the
steps and writes the seams for you. Manual reference:
[README → Forking](README.md#forking-nexus-for-your-business) has the ordered 8 steps.
The three primary seams: `vault/schema.py`, `connectors/ingress/rules.py`,
`connectors/<source>/`. Registering a new connector = add its `webhook` module to
`ingress.routes.CONNECTORS`, its reads to `agents/toolset.py` + `tools/__init__.py`, and (if
it polls) its sync to `jobs.DETERMINISTIC_JOBS`. When you touch the schema, re-run `pytest`.

## Remaining stubs (fill in a fork)

`connectors/example/client.py` (outbound HTTP), `connectors/example/sync.py` (poll-sync),
binary-format extractors in `ingest/extract.py` (text/HTML handled), the `notify` transport
in `agents/notify.py` (logs by default), and the Batches-API path in `ingest/batch.py`
(a drop-in optimization over the working per-file loop). Each carries a
`NotImplementedError("§…")` pointing at its contract.
