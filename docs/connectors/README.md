# Connector intake & planning

A **connector** is one package (`nexus/connectors/<source>/`) that speaks one external
system's dialect. Every source is different — some sign webhooks, some only offer polling,
some have an official MCP server you can borrow, some need OAuth. **You don't have to know
how to build it to plan it.** This folder is where each source's plan lives before anyone
writes connector code.

The `/onboard` interview fills one file per source here using the template below. A later
agent (or you) then implements each connector by following that file. Keep the plan and the
code in sync.

---

## First decision: do you even need to build one?

Before planning a custom connector, check for something you can borrow:

1. **Is there an official/native MCP server or a desktop-client integration** (e.g. Gmail,
   Google Calendar, Slack, Notion)? → Use it for the **conversational** path. The agents
   just `create_task` (with `channel` + `recipient`) and the owner executes through that
   native tool. **No custom connector needed.** Note it and move on.
2. **Do you need server-side, signed, real-time ingestion, or programmatic reads a native
   tool can't give?** → Build a custom connector. Continue below.

## Second decision: push or pull?

| If the source… | Shape | You implement |
|---|---|---|
| signs & POSTs events in real time (webhooks) | **push** | `webhook.py`: `secret()` + `parse()` (+ `signed_timestamp()`) |
| has no reliable webhooks, but a queryable API | **pull** | `sync.py`: a deterministic poll-sync (no LLM) that upserts by `source_ref` and *manufactures* deltas |
| both | both | `webhook.py` for live events **and** `sync.py` for backfill/reconciliation |

Either way, if the agent needs to pull live detail on demand, add a typed **read** client
(`client.py`) and wrap its read methods as agent tools. **Never** wrap write/send methods —
outbound mutations are external-facing and go through the approval queue.

## Third decision: auth

| Auth model | Where it goes | Notes |
|---|---|---|
| Webhook HMAC secret | `<SOURCE>_WEBHOOK_SECRET` env → `secret(cfg)` | constant-time verify is handled for you |
| URL token (no body signing) | secret in the callback path | vendors that don't sign the body |
| API key / bearer (reads) | `<SOURCE>_API_KEY` env → `client.py` | simplest outbound |
| OAuth2 | token store on the volume + a `scripts/authorize.py` flow | plan the grant type + scopes; refresh handling |

---

## Intake template — copy to `docs/connectors/<source>.md`

```markdown
# Connector: <source>

## What & why
- Vendor / system: <name>
- What it holds: <data — e.g. leads, appointments, calls, invoices>
- Feeds these vault kinds/reference: <entity kinds / reference categories>
- Business events that matter: <e.g. new lead, appointment booked, call missed>

## Shape
- Direction: [ ] inbound (events)  [ ] outbound (reads)  [ ] both
- Native/MCP alternative exists? <yes/no — if yes, prefer the escape hatch>
- Push or pull: <push webhooks | pull poll-sync | both>

## Inbound (if push)
- Webhook docs URL: <link>
- Signature scheme: <HMAC-SHA256 header? URL token? none?>
- Signature header name: <x-...-signature>
- Signs a timestamp? <yes/no → enables replay window>
- Subscription mechanism: <dashboard toggle | API call | scripts/subscribe.py needed>
- Event types → our `kind`s: <vendor.type -> our_kind, ...>
- Idempotency id field: <payload path used for external_id>

## Outbound (if reads / poll-sync)
- API docs URL: <link>
- Auth: <API key | OAuth2 (grant + scopes) | bearer>
- Rate limits / pagination: <notes>
- Poll-sync high-water field: <updated_at / cursor used to fetch only changes>
- Read endpoints the agent needs: <list -> map to acme_search_x / acme_get_x tools>

## Risk tier (rules.py)
- (source, kind) → tier: <supervised | log_flag | autonomous>  (default supervised)

## Secrets to add to .env
- <SOURCE>_WEBHOOK_SECRET / <SOURCE>_API_KEY / OAuth client id+secret

## Implementation steps (for the next agent)
1. <copy connectors/example/ → connectors/<source>/>
2. <webhook.py: NAME, SIGNATURE_HEADER, secret(), parse(), _KIND_MAP>
3. <client.py: typed read methods>  /  <sync.py: poll-sync + high-water>
4. <register in ingress.routes.CONNECTORS (+ jobs.DETERMINISTIC_JOBS if polling)>
5. <wrap reads in agents/toolset.py + tools/__init__.py>
6. <add rows to connectors/ingress/rules.py>
7. <add secrets to .env / .env.example>
8. <test: send a sample payload; verify parse → Stimulus → log → dispatch>

## Open questions / unknowns
- <anything the owner needs to find out: API access, plan tier, admin rights>
```

---

## What "done" means for onboarding

The goal of the interview is **not** working connector code. It is a complete, unambiguous
plan per source: shape, auth, event mapping, secrets, and ordered implementation steps, with
open questions flagged. When each `docs/connectors/<source>.md` is filled to that bar, a
later agent can implement it without re-interviewing the owner.
