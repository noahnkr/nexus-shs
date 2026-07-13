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
  (OpenAPI spec: https://crm.welcomehomesoftware.com/api-docs/v1/swagger.yaml)
- Auth (CONFIRMED from the spec): `Authorization: Token token={api_key}` header; validate
  with `GET /api/ping` (200 -> `{"account_id": ..}`, 401 -> bad credentials).
- Endpoint (CONFIRMED): `GET /api/exports/community/all/table/Prospects` returns live,
  paginated CSV. Default 1000 rows/page (max 10000); the next page is a cursor URL in the
  `Link` response header (`rel="next"`); a cursor can only be used 3x per minute. All
  timestamps UTC.
- Poll-sync high-water field (CONFIRMED): `updated_at` — the endpoint documents
  `filters[updated_at_after]` as the intended re-poll strategy and sorts by `updated_at`
  by default.
- Stages are ACCOUNT-CONFIGURABLE: `GET /api/stages` returns id/name/position/system_type;
  the sync translates `stage_id` -> name -> vault `Status` via a normalization table
  (`sync._STAGE_TO_STATUS`). `GET /api/lead_sources` likewise maps `lead_source_id` ->
  referral source name.
- Read endpoints the agent needs: none beyond the export for now — the poll-sync reconciles
  bulk state into `prospect` entities directly; no live on-demand read tool
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
- `WELCOMEHOME_API_KEY` — sent as `Authorization: Token token={key}` (confirmed scheme)

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

## Verified against the OpenAPI spec (swagger.yaml, checked 2026-07-13)
- Auth, ping, export endpoint/pagination/cursor limits, `filters[updated_at_after]`,
  `sort_by=updated_at` default, and `community_id=all` all match the implementation.
- `status` enum is exactly `open | draft | closed | moved_in | marketing_qualified`; the
  spec says to ignore `draft` (mid-creation) and `marketing_qualified` (not user-visible),
  which the sync does. `moved_in` "could represent a former resident."
- `/prospects` is POST-only (create); there is no list-GET — reads must go through the
  export (or `/prospects/search` / `/prospects/{id}`), confirming the poll-sync design.
- Prospects carry `discarded_at` / `discarded_by_id` / `merged_into_prospect_id`: the sync
  treats a discarded or merged-away row as defunct — archives a tracked entity, never
  creates one, and emits no delta.
- The Prospect record has NO top-level name/phone/email — those live on nested
  `residents_attributes[].person_attributes` (and `influencers_attributes` for family).

## Verified against a LIVE export (real token, checked 2026-07-13)
- CSV headers are TABLE-PREFIXED: `prospects.id`, `prospects.status`,
  `prospects.inquiry_date`, `prospects.discarded_at`, `stages.name`, `lead_sources.name`,
  `communities.name`, ... — `sync._map_row` matches these first (bare names kept as
  fallbacks). Stage and lead-source NAMES are inlined in the row, so the `/stages` +
  `/lead_sources` id→name maps are only a fallback.
- The Prospects export carries NO `updated_at` column (the server-side
  `filters[updated_at_after]` still works) and NO resident name/contact columns. The sync
  therefore (a) advances the high-water mark to poll-start-time minus a 5-minute overlap
  (`sync._REPOLL_OVERLAP`) — re-pulled rows are quiet since an unchanged upsert emits no
  delta — and (b) joins the `Residents` export (`residents.prospect_id` → `people.first/
  last_name`, `people.cell_phone`, `people.email`; `first_resident` row wins) for
  name/phone/email.
- No last-contact column either: `last_contact_date` is derived from the `Activities`
  export (`record_type == "Prospect"`, max `completed_at` per `record_id`), pulled
  incrementally with the same mark; it only moves forward.
- This account's actual stage labels (from `/stages`): Inquiry → Contact Attempted →
  Contact Made → Home Visit Scheduled → Home Visit Completed → Start of Care. All six are
  in `sync._STAGE_TO_STATUS`.

## Open questions / unknowns
- ~~Exact auth scheme~~ RESOLVED: `Authorization: Token token={api_key}` (see Outbound).
- ~~Exact CSV column HEADER names~~ RESOLVED against a live export — see "Verified against
  a LIVE export" above.
- ~~Stage mapping~~ RESOLVED: this account's six stage labels are all in
  `sync._STAGE_TO_STATUS` (verified via `/api/stages`).
- Definition of "stale" for the `prospect_stale` delta — provisionally
  `sync.STALE_AFTER = 2 days` without contact/movement (vault dates are day-granular);
  confirm the window with Brennen once the sync is running.
- Family contacts (influencers) are not populated by the sync yet — the `Influencers`
  export table exists and could be joined on prospect id the same way `Residents` is.
