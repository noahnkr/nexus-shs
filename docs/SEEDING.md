# Seeding checklist — Seniors Helping Seniors, Greater Naperville

*What to gather before go-live, and in what order. An empty vault makes a dumb agent —
reference goes in first so the agent has vocabulary and judgment before it starts holding
state.*

## 1. Reference (author/ingest first)

Ingest with `ingest_file(path, overrides={"category": ..., "audience": ...}, subfolder=...)`
or `ingest_batch([...], overrides=...)` for a folder — `overrides` pins frontmatter you
already know over the classifier's guess. Drafts land as `status: draft`; a human promotes
to `published` after review — via chat (the `set_note_status` MCP tool; `list_reference`
with `status="draft"` shows what's pending) or by editing the frontmatter. Both ingest
functions are also MCP tools, so seeding can happen entirely from a chat client pointing
at server-local files.

- [ ] **Intake / sales scripts** (`category=intake_script`) — how to talk to a new inquiry
      at each WelcomeHome stage: qualifying questions, objection handling, what to say when
      scheduling a visit. This is the highest-leverage doc since leads are time-sensitive.
- [ ] **Pricing** (`category=pricing`) — hourly rates by service line (Companion Care,
      Personal Care, Respite Care, Specialized Care, VA Prospect), minimum hours, any
      packages or VA-benefit specifics.
- [ ] **Service SOPs** (`category=service_sop`) — one entry per service line describing
      what it actually includes, so the agent can answer questions accurately without
      guessing.
- [ ] **Company policy / voice** (`category=policy_voice`) — brand voice/tone guide (feeds
      how drafts should read — formal/professional per `vault/context/SOUL.md`), general
      policies, and any FAQs staff currently use with prospects/families.
- [ ] All of the above tagged `audience=internal` unless you specifically want something
      shareable verbatim with prospects/families (`audience=client_facing`).

## 2. Entity state — `prospect`

Where it comes from: **WelcomeHome CRM** is the source of truth. Until the WelcomeHome
poll-sync connector is implemented (`docs/connectors/welcomehome.md`), load an initial
snapshot by hand:

- [ ] Export current open Prospects from WelcomeHome (the same bulk Exports API the
      connector will eventually use, or a manual CSV export in the meantime).
- [ ] For each, create a `prospect` entity note with: `title` (prospect name), `status`
      (mapped to `inquiry`/`attempted`/`contact_made`/`visit_scheduled`/`visit_completed`/
      `soc`), `referral_source`, `service_lines`, `phone`, `email`, `family_contacts`
      (name/phone/email for each family decision-maker), `inquiry_date`,
      `last_contact_date`, `next_follow_up`.
- [ ] **No medical/PHI detail** — leave diagnoses, medications, and detailed care records
      in WellSky; at most a brief non-sensitive note in the body (e.g. "has mobility
      needs").
- [ ] Once the WelcomeHome poll-sync is implemented, it takes over reconciliation and this
      manual load becomes a one-time backfill.

## 3. Leave the event log empty

Don't backfill history — the event log starts recording from go-live forward per the
"log always" invariant.

## 4. Go-live order

1. Ingest reference material (step 1) — the agent needs vocabulary and judgment before it
   holds any state.
2. Load current open Prospects (step 2) so the agent isn't starting blind on the pipeline.
3. Implement and test the WelcomeHome poll-sync connector
   (`docs/connectors/welcomehome.md`) so Prospect state stays current automatically.
4. Implement the GoTo Connect connector (`docs/connectors/goto-connect.md`) for missed-call
   / SMS visibility.
5. Set real secrets (`.env`) and deploy — see `docs/DEPLOYMENT.md`.
