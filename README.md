# Nexus — Seniors Helping Seniors, Greater Naperville

**The operating intelligence for Seniors Helping Seniors — Greater Naperville**, a home
care franchise serving seniors and their families with companion, personal, and
specialized care.

This fork keeps a persistent, queryable vault of the sales pipeline — leads from
aggregators (A Place for Mom, Care.com, etc.) through WelcomeHome CRM's stages (Inquiry →
Attempted → Ct Made → Visit Schld → Visit Cmplt → SOC) — and agents that log, flag, and
draft on top, so nothing time-sensitive falls through the cracks. Owner: Brennen Roberts
(brennen@shsgreaternaperville.com).

It's built on **Nexus**, a forkable foundation for building a *system of intelligence*
over any business: a layer that sits **above** your systems of record (CRM, phone, email,
calendar) and synthesizes them into one queryable, self-maintaining context — with agents
that notify, log, queue, and act on top.

## Reconfiguring this fork

This fork has already been through the onboarding interview (see
`docs/onboarding/PROFILE.md` for the raw record of decisions). If the business changes in a
way that needs reconfiguring — new entity kinds, a new connector, a change in risk
tolerance — open this repo in [Claude Code](https://claude.com/claude-code) and run
`/onboard` again, or edit the seams by hand per [Forking](#forking-nexus-for-your-business)
below.

---

## Why it's different from "AI over your documents"

Most "AI over your docs" stops at RAG: upload files, retrieve chunks at query time,
generate an answer. The model rediscovers everything from scratch every time. Nothing
accumulates.

Nexus keeps a persistent, interlinked markdown **knowledge base** (the vault) that the LLM
*incrementally builds and maintains* — synthesis is compiled once and kept current, not
re-derived per query. On top of that wiki it adds three things a knowledge base alone
doesn't have:

1. **Enforced structure.** A Pydantic schema is the single source of truth for note shape,
   so machine-written state stays valid and queryable — not just prose.
2. **Evolving state, not just reference.** Alongside authored knowledge it holds
   machine-written **entity state** (a knowledge graph of what you track), an append-only
   **event log** (event sourcing), and a **task queue** (an approval inbox).
3. **Bounded autonomy.** Ambient agents that don't only answer when asked but *wake on
   events and schedules*, act under a single trust rule, and route anything consequential
   to a human first.

---

## The mental model: one stack, one pass

Everything Nexus does is one pass down this stack. A chat message, a signed webhook, and a
cron tick are the *same kind of thing* — they all normalize to one `Stimulus` and run the
same loop.

```
 SOURCES   a CRM · a phone system · a scheduler · an inbox · a human in chat
    │        each speaks its own dialect
    ▼
 EXTERNAL   connectors/<source>/ — verify + parse inbound · typed client for outbound
    │        every inbound thing becomes one HTTP request
    ▼
 INGRESS    connectors/ingress/ — authenticate → normalize to Stimulus → classify risk
    │        → LOG ALWAYS → ACK <3s → dispatch a worker (background)
    ▼
 AGENTIC    agents/ — three agents, ONE six-stage loop:
    │        receive → plan → gather → decide → deliver → record
    ▼
 KNOWLEDGE  vault/ — REFERENCE (authored) · ENTITY (state) · EVENTS (log) · TASKS (queue)
    │        schema-enforced · indexed · hybrid retrieval · gated writes
    ▼
 OUTPUTS    notify owner · vault write · queue for approval · autonomous act
                              (governed by ONE trust rule)
```

### The load-bearing ideas (they survive every fork)

- **A stimulus is a stimulus.** One `Stimulus` envelope for every entry point; nothing
  downstream branches on origin.
- **One loop, everywhere.** Every agent runs the identical six stages; only what arrives
  (stage 1) and what goes out (stage 5) differ.
- **The schema is the contract.** One Pydantic model set generates the JSON schema that
  constrains the LLM, the templates, and the validator every write crosses.
- **Reference is authored; state is accumulated.** Two kinds of knowledge, deliberately
  separated so the agent can reason about *where* to look.
- **The tools are the router.** No central `search_everything`; each layer has one narrow,
  precisely-described tool and routing *emerges* from the model reading them.
- **Log always; write only change.** Every stimulus is appended to the event log no matter
  what; everything else is gated by "did anything actually change?"
- **One trust rule governs autonomy.** *External-facing → human approval; vault-only or
  owner-only → autonomous.* Anything outward-facing is **structurally incapable of sending
  itself** — it can only become a queued draft. The boundary is the *absence of a
  send tool*, not a prompt the model might forget.
- **Determinism where it matters.** Risk classification is a static table, never an LLM
  call. Auth is constant-time. The model does judgment, not plumbing.
- **Autonomy is earned.** Start fully supervised; promote a specific `(source, kind)` to
  autonomous only after a track record.
- **One process, one volume, no new infra.** Search is in-process; cron is an HTTP call.
  Reach for a broker or vector cluster only when scale forces it.

---

## The four note families

The vault holds four kinds of note; the split is the whole point.

| Family | Nature | Who writes it | Purpose |
|---|---|---|---|
| **reference** | authored, slow | humans (via ingest) | SOPs, policy, pricing, voice, domain knowledge |
| **entity** | state, evolving | machine | current distilled state of each person/org/thing you track |
| **event** | append-only history | machine | the permanent, undeletable audit trail (one note/day) |
| **task** | pending decisions | machine | the human-approval queue |

Notes are markdown + YAML frontmatter, versioned in git, browsable in
[Obsidian](https://obsidian.md).
---

## Quickstart (local)

```bash
pip install -e ".[dev]"
cp .env.example .env          # fill in secrets (see below)
uvicorn nexus.app:app --reload
curl localhost:8000/health    # -> {"status":"ok","env":"dev"}
pytest                        # acceptance tests
ruff check nexus tests        # lint
```

Nexus runs **with no API key at all** (lexical BM25 search, semantic dormant). To light up
the agents and semantic search, set keys in `.env`:

- `ANTHROPIC_API_KEY` — powers the six-stage agent loop and the ingest classifier.
- `EMBEDDING_API_KEY` — optional (Voyage); enables semantic search. Omit for BM25-only.

Everything configurable is documented in [`.env.example`](.env.example).

Point a desktop MCP client at `/<PUBLIC_URL>/mcp` (Streamable HTTP) to chat with your
vault; the same read/write functions back both chat and the ambient agents.

---

## Project layout

```
nexus/                     # the server package
├─ app.py                  # entrypoint: Starlette routes + /health + mounted /mcp
├─ config.py               # typed settings (env), prod fail-fast
├─ writes.py               # the write surface — every write crosses the gate
├─ vault/                  # KNOWLEDGE LAYER
│  ├─ schema.py            #   the contract — your domain vocabulary   (FORK HERE)
│  ├─ io.py                #   write gate + shared vault helpers
│  ├─ index.py             #   generated INDEX.md (leaf / branch / calendar)
│  ├─ search.py            #   in-process BM25 + sqlite-vec + RRF
│  ├─ embeddings.py        #   swappable embed() (Voyage; dormant w/o key)
│  ├─ queries.py           #   the five read tools
│  └─ events.py            #   append-only day log
├─ connectors/
│  ├─ ingress/             #   INGRESS LAYER — the reusable front door
│  │  ├─ rules.py          #     (source,kind) → risk tier            (FORK HERE)
│  │  └─ …                 #     envelope · security · router · routes · jobs
│  └─ example/             #   a sample connector — copy per source   (FORK HERE)
├─ ingest/                 # extract → classify → draft note → archive → reindex
├─ agents/                 # AGENTIC LAYER — six-stage loop + reactive/scheduled + context
└─ tools/                  # MCP wrappers over the same plain functions

vault/                     # the SEED vault (reference + context + generated indexes)
docs/                      # DEPLOYMENT.md · connectors/ intake plans · SEEDING (after /onboard)
scripts/                   # human-run CLIs
.claude/commands/          # /onboard — the setup interview
```

Depth on *how it all works* and *how to change it* lives in
[`CLAUDE.md`](CLAUDE.md) — it doubles as the engineering reference for humans and agents.

---

## Forking Nexus for your business

**The guided path:** run [`/onboard`](.claude/commands/onboard.md) and let the interview do
the steps below from your answers. The manual reference is here for when you want to edit a
seam directly. Either way the core loop, ingress, gate, retrieval, and index machinery are
**untouched** — instantiating a domain is a bounded, ordered set of edits:

1. **Model your domain** in `nexus/vault/schema.py` *(the big one)* — your entity `Kind`s,
   lifecycle statuses, reference taxonomy, and any data boundary (PHI/secrets). Everything
   downstream (JSON schema, templates, validator, tool hints) follows automatically.
2. **Set your risk policy** in `nexus/connectors/ingress/rules.py` — one row per
   `(source, kind)`; default everything to `supervised`, relax only what you've proven.
3. **Add a connector per source** under `nexus/connectors/<source>/` — copy `example/`:
   `secret()` + `parse()` inbound, a typed read client outbound, a poll-sync if the source
   lacks webhooks.
4. **Expose the connector's reads** as agent tools (`agents/toolset.py`,
   `tools/__init__.py`). Never add send/write tools — those stay external-facing.
5. **Tune the agent prompts** (`agents/reactive.py`, `agents/scheduled.py`) and the
   persona/owner files in [`vault/context/`](vault/context) — one lean paragraph each.
6. **Choose cron jobs** in `nexus/connectors/ingress/jobs.py`.
7. **Seed the vault** — ingest foundational reference docs and author initial entity state
   before go-live. An empty vault makes dumb agents.
8. **Configure & deploy** — set env, one service, one volume. See
   [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

> **Do not touch** the six-stage loop, the `Stimulus` envelope, the ingress route handlers
> and security primitives, the index renderers, the hybrid-search engine, the write gate,
> or the trust-gate mechanism. If a fork finds itself editing those, the change belongs in
> a seam. Full rationale and mechanics: [`CLAUDE.md`](CLAUDE.md).

---

## Deploying

One service, one volume. Railway is the natural fit; generic Docker works anywhere. The
repo's `vault/` is a **seed** — on first boot it's copied into the mounted volume once and
never clobbered after, so live state survives redeploys. Full guide, env matrix, and cron
setup: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Status

Implemented and verified end-to-end (`pytest`, `ruff` clean): host + `/health` + `/mcp`,
the full Knowledge layer, the full Ingress layer (signed webhook → log-always → fast ACK →
dispatch; bearer cron), and the Agentic layer (six-stage loop, reactive/scheduled agents,
MCP surface). The agent loop and ingest classifier activate with `ANTHROPIC_API_KEY`;
semantic search with `EMBEDDING_API_KEY`. Connector HTTP clients, poll-syncs, binary-format
extractors, and the notify transport are marked stubs a fork fills in.
