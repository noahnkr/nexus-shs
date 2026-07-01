# Connector: welcomehome

## What & why
- Vendor / system: WelcomeHome CRM (crm.welcomehomesoftware.com)
- What it holds: leads ("Prospects") and their pipeline stage. Aggregator leads (A Place
  for Mom, Care.com, etc.) arrive as structured emails that WelcomeHome already auto-intakes
  — Nexus does not need to parse those emails itself, only read WelcomeHome's resulting
  Prospect records.
- Feeds these vault kinds/reference: entity kind `prospect` (`nexus/vault/schema.py`).
- Business events that matter: a new Prospect created; a Prospect's stage advances
  (Inquiry → Attempted → Ct Made → Visit Schld → Visit Cmplt → SOC); a Prospect goes stale
  (no stage movement / no `last_contact_date` update within an expected window — leads are
  time-sensitive and should be responded to within the hour).

## Shape
- Direction: [x] outbound (reads)  — no inbound webhooks available
- Native/MCP alternative exists? No known native/MCP integration for WelcomeHome.
- Push or pull: **pull only**. WelcomeHome has no webhooks for stage changes; it exposes a
  live, paginated bulk CSV export endpoint
  (https://crm.welcomehomesoftware.com/api-docs/index.html#tag/Exports). This is a
  poll-sync (`sync.py`), not a webhook (`webhook.py`).

## Inbound (if push)
- N/A — no push path for this source.

## Outbound (if reads / poll-sync)
- API docs URL: https://crm.welcomehomesoftware.com/api-docs/index.html#tag/Exports
- Auth: unconfirmed — check the Exports API docs for the auth scheme (likely API key or
  bearer token issued per WelcomeHome account). Open question below.
- Rate limits / pagination: the export endpoint is paginated; exact page size / rate limit
  unconfirmed — read the docs above when implementing.
- Poll-sync high-water field: use WelcomeHome's Prospect `updated_at` (or export's
  equivalent last-modified column) as the high-water mark so re-polls only fetch changed
  rows. Confirm the exact column name against the export schema.
- Read endpoints the agent needs: none beyond the export for now — the poll-sync reconciles
  bulk state into `prospect` entities directly (§4.3 pattern); no live on-demand read tool
  is needed at this stage since the export already gives current Prospect state.

## Risk tier (rules.py)
- `(welcomehome, new_prospect)` → `log_flag` — vault-only entity create, but flagged since
  a new time-sensitive lead just arrived and Brennen should notice fast.
- `(welcomehome, stage_changed)` → `log_flag` — vault-only entity update.
- `(welcomehome, prospect_stale)` → `log_flag` — a manufactured delta from the poll-sync
  when a Prospect hasn't moved/been contacted within the expected window; flags for
  follow-up. Nothing here is external-facing (WelcomeHome itself isn't written to), so
  nothing needs `supervised`.

## Secrets to add to .env
- `WELCOMEHOME_API_KEY` (or bearer token — confirm exact scheme from the Exports API docs)

## Implementation steps (for the next agent)
1. Copy `nexus/connectors/example/` → `nexus/connectors/welcomehome/`; keep only
   `client.py` (read methods) and `sync.py` (poll-sync) — no `webhook.py`, since there's no
   push path.
2. Read the Exports API docs (link above) to confirm: auth scheme, pagination shape, the
   column WelcomeHome uses for "last updated," and how a Prospect's current stage is
   represented in the export rows.
3. `client.py`: a typed `WelcomeHomeClient` with one read method, e.g.
   `export_prospects(since: datetime | None) -> list[dict]`, paginating through the export
   endpoint. No write/send methods — WelcomeHome is read-only from Nexus's side.
4. `sync.py`: implement `run_sync()` — read the stored high-water mark, call
   `export_prospects(since=high_water)`, map each row to `nexus.writes.update_entity(kind="prospect", ...)`
   keyed by `source_ref="welcomehome:prospect:<id>"`, advance the high-water mark. For rows
   where the stage advanced or the Prospect is new, build a `Stimulus(source="welcomehome",
   kind="new_prospect"|"stage_changed", ...)` and call `ingress.router.dispatch()` so the
   reactive agent wakes without an LLM call per reconciled row. NO LLM in this path.
5. Register `run_sync` in `nexus/connectors/ingress/jobs.py` → `DETERMINISTIC_JOBS`, on a
   short interval (e.g. every 5–10 minutes) given the "respond within the hour" expectation.
6. Add the three rows above to `nexus/connectors/ingress/rules.py`.
7. Add `WELCOMEHOME_API_KEY` to `.env` / `.env.example`.
8. Test: run `run_sync()` against a WelcomeHome sandbox/test account (or a captured export
   sample) and verify Prospects land as `prospect` entity notes with the correct `status`,
   and that a stage change produces a dispatched `Stimulus`.

## Open questions / unknowns
- Exact auth scheme for the Exports API (API key vs bearer vs OAuth) — check docs / ask
  WelcomeHome support.
- Exact column names in the export for Prospect id, stage, `updated_at`, referral source,
  and family contact(s) — confirm against a real export sample.
- Whether "stage" in the export maps 1:1 to the six stages already modeled in `Status`
  (`inquiry`/`attempted`/`contact_made`/`visit_scheduled`/`visit_completed`/`soc`), or uses
  different internal labels that need a translation table.
- Definition of "stale" for the `prospect_stale` delta (how long without contact/movement
  before flagging) — confirm with Brennen once the sync is running.
