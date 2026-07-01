---
description: Interview the owner and configure this Nexus fork for their business
---

# Nexus onboarding interview

You are conducting the **onboarding interview** for a fresh fork of Nexus. Across a short
conversation you will learn who the owner is and how their business works, then turn that
into a configured, ready-to-seed system: filled context files, a domain schema, a risk
policy, per-source connector plans, tailored docs, and a seeding checklist.

Read `README.md` and `CLAUDE.md` first so you understand the architecture you're
configuring. Then run the interview.

## How to run the interview

- **Be a person, not a form.** Warm, efficient, curious. Ask **2–4 questions at a time**,
  in plain language, and react to the answers before moving on. Never dump the whole list.
- **Use `AskUserQuestion`** when the choice is bounded (e.g. "push webhooks vs polling",
  "supervised vs autonomous"). Use open questions for anything descriptive.
- **Reflect back.** After each phase, summarize what you heard in a sentence or two and let
  them correct you. Their words are the source of truth — capture their vocabulary verbatim.
- **Adapt and skip.** If a branch doesn't apply (no phone system, no CRM), drop it. If they
  give you everything in one answer, don't re-ask.
- **Don't build connectors here.** For each external system you gather enough to *plan* it
  (auth, webhook vs poll, event mapping) — you do **not** implement client/sync logic now.
- **Confirm before writing.** When a phase is done, tell them what you're about to write,
  then write it. Keep the context files (SOUL/USER/ORG) short — they're paid on every turn.
- **Keep a running record.** As you go, capture raw answers in
  `docs/onboarding/PROFILE.md` so nothing is lost and a later agent can re-read decisions.

Start by orienting them: one short paragraph on what this interview will produce (~20–30
min, results in their fork being configured), then begin Phase 1.

---

## Phase 1 — You & your voice  → `vault/context/SOUL.md`, `vault/context/USER.md`

Ask about:
- Their name, role, and how they want to be addressed/notified (maps to `OWNER_CONTACT`).
- How the assistant should *sound* — tone, formality, verbosity. Terse or warm? Emoji?
- Standing preferences and hard rules ("never text clients after 8pm", "always CC
  bookkeeping", timezone, working hours).
- Autonomy appetite in general: does this owner want the agent to act, or to draft and wait?

**Write:** `SOUL.md` (persona, voice, priorities — rewrite the template in the owner's
tone) and `USER.md` (owner facts, reach, preferences, standing instructions). Delete the
template's `FORK:` instruction lines. Keep each to a screen.

## Phase 2 — The business  → `vault/context/ORG.md`

Ask about:
- What the business does, for whom, and how it makes money (the flow of work in).
- Core offerings/services and the customers.
- Operating rhythm (hours, timezone, busy seasons, response-time expectations).
- The systems of record where truth lives today (CRM, scheduler, phone, inbox, billing).
- The 3–8 domain terms the agent must use correctly (a small glossary).

**Write:** `ORG.md` from the template — concise, stable facts only. Anything large or
fast-changing is a `reference/` note later, not context.

## Phase 3 — What you track  → entity schema in `nexus/vault/schema.py`

This defines the **entity** side of the vault: the people/things whose *current state* the
agent maintains. For each distinct thing they track, elicit:
- **Kind** — a singular noun (client, provider, deal, patient, property, matter…).
- **What it represents** and how they'd recognize two records as the same one (identity /
  dedup key → informs `source_ref`).
- **Lifecycle** — the stages/statuses it moves through (become `Status` values).
- **Filterable fields** — the handful of attributes they'd want to *filter or sort by*
  (owner, stage, area, dates, amount, priority). Capture name + type for each.
- **Relationships** — links to other kinds (a deal's account, a patient's provider). These
  become typed `[[wikilink]]` frontmatter fields.

Push back gently on over-modeling: **only fields you'd filter on or must always see** belong
in frontmatter; everything else is prose in the note body. Aim for 3–6 fields per kind.

**Write:** edit `nexus/vault/schema.py` — replace the placeholder `Kind` with their kinds;
model each kind as an `EntityNote` subclass carrying its filterable fields; extend `Status`
with their lifecycle values; combine kinds into the entity discriminated union (on `kind`)
per the pattern documented in that file. Then **run `pytest -q` and `ruff check`** — the
schema is the contract; it must stay green. Show them a sample rendered note (`template_for`)
for one kind and confirm it matches their mental model.

## Phase 4 — What you know  → reference taxonomy in `nexus/vault/schema.py`

The **reference** side: authored knowledge (SOPs, policy, pricing, scripts, voice, FAQs).
Ask about:
- The categories of documents they'd want the agent to know (→ `ReferenceNote.category`
  values and, optionally, `reference/<subfolder>/` for human browsing).
- Audiences, if relevant (internal vs client-facing).
- Any **data boundary** — information that must *never* enter the vault (PHI, card numbers,
  privileged/legal, secrets). This becomes both a schema note and a classifier instruction.

**Write:** update `ReferenceNote` (category/audience as enums or documented strings) and
record any data boundary prominently in `ORG.md` and as a `FORK` note in `schema.py`.

## Phase 5 — Your systems  → `docs/connectors/<source>.md` (one per source)

For each external system worth connecting, walk the intake in
[`docs/connectors/README.md`](../../docs/connectors/README.md). Per source, determine:
- **Is there a native/MCP integration to borrow?** (the §4.5 escape hatch — prefer it).
- **Push or pull** — does it sign & POST webhooks, or must you poll?
- **Auth** — HMAC webhook secret, API key/bearer, or OAuth (grant + scopes).
- **What events matter** and how they map to the owner's vocabulary (vendor type → `kind`).
- **What the agent needs to read** live (→ read tools) vs. reconcile in bulk (→ poll-sync).
- **Unknowns** they must resolve (API access, plan tier, admin rights).

Don't require them to know the technical details — infer what you can from the vendor name,
ask only what matters, and record unknowns as open questions.

**Write:** one `docs/connectors/<source>.md` per system, filled from the template, including
ordered implementation steps for the *next* agent. Do **not** implement connector code.

## Phase 6 — Risk & autonomy  → `nexus/connectors/ingress/rules.py`

For each `(source, kind)` event surfaced in Phase 5, decide its trust tier with the owner:
- `supervised` — external-facing; becomes a draft the owner approves (the safe default).
- `log_flag` — vault-only state update plus a flag for attention.
- `autonomous` — owner-only/vault-only; runs without approval.

Default everything to **supervised** and only relax what the owner is confident about.
Confirm the notification transport (`agents/notify.py` / `OWNER_CONTACT`).

**Write:** rows in `rules.py`. Note in each connector doc which tiers were chosen.

## Phase 7 — Wrap up  → tailor docs, produce the seeding plan

1. **Tailor the docs.** Update `README.md`'s title/tagline/intro to name the business and
   what this instance does (keep the framework explanation). Fill `.env.example` comments
   for the secrets their connectors need.
2. **Verify.** Run `pytest -q` and `ruff check nexus tests`; report the result.
3. **Write `docs/SEEDING.md`** — a concrete, checklist-style plan of exactly what to gather,
   tailored to their answers:
   - **Reference to author/ingest first**, by category (e.g. "pricing sheet", "intake SOP",
     "top-20 FAQs", "tone/voice guide") — reference before entities.
   - **Entity state to load**, per kind (e.g. "active clients with stage + owner", "current
     open deals") — where it comes from (export? connector backfill via poll-sync?).
   - **Leave the event log empty** until go-live.
   - How to ingest (`ingest_file` / `ingest_batch`, `subfolder=`), and that drafts are
     promoted to `published` by a human.

4. **Explain, out loud, to the owner** what they now need to collect to make the agent
   useful: which reference documents and which entity records, in what order, and why an
   empty vault makes a dumb agent. This spoken summary is the payoff of the interview.

## Final output

End with a short recap: files written/edited (SOUL/USER/ORG, schema, rules, connector
plans, README, SEEDING), test status, and the **top 3 next actions** — usually (1) gather
the seed material in `docs/SEEDING.md`, (2) implement the highest-value connector from its
`docs/connectors/<source>.md` plan, (3) set secrets and deploy (`docs/DEPLOYMENT.md`).
