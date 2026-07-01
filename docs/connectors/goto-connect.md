# Connector: goto-connect

## What & why
- Vendor / system: GoTo Connect (VoIP + SMS phone system)
- What it holds: call activity (inbound/outbound/missed) and SMS messages with prospects,
  clients, and families.
- Feeds these vault kinds/reference: entity kind `prospect` (updates like
  `last_contact_date`, and a `family_contacts` phone match); the event log (every call/text
  logged, per the "log always" invariant) regardless of whether it changes a `prospect`.
- Business events that matter: a missed call from a number matching a known prospect/family
  contact; an inbound SMS from a prospect/family contact. Both are time-sensitive given the
  "respond within the hour" expectation on leads.

## Shape
- Direction: [x] inbound (events)
- Native/MCP alternative exists? Not confirmed — GoTo Connect has a developer API
  (developer.goto.com); no known desktop/MCP integration for it. Build a custom connector.
- Push or pull: **push**, if GoTo Connect's developer API supports webhooks for call/SMS
  events (it does, per their developer docs — confirm exact event types when implementing).
  If webhook coverage turns out to be partial, add a `sync.py` poll fallback for call
  history.

## Inbound (if push)
- Webhook docs URL: https://developer.goto.com/ (GoTo Connect / Voice APIs — confirm the
  exact webhooks product page and event catalog when implementing; Brennen can obtain
  developer/API access).
- Signature scheme: unconfirmed — check GoTo's webhook docs for their signing scheme
  (commonly HMAC-SHA256 over the raw body, or OAuth-scoped webhook subscriptions with no
  body signature). Confirm before wiring `secret()`.
- Signature header name: unconfirmed — fill in from docs.
- Signs a timestamp? unconfirmed — fill in from docs; enables the replay window if present.
- Subscription mechanism: likely an API call to register a webhook subscription per event
  type (GoTo's APIs are typically OAuth2 + REST). Confirm and, if needed, add a
  `scripts/subscribe.py` to (re)register subscriptions.
- Event types → our `kind`s: e.g. `call.missed` → `missed_call`, `message.received` →
  `sms_received`. Confirm GoTo's actual event type names against their API docs.
- Idempotency id field: GoTo's call/message id (confirm field name from payload).

## Outbound (if reads / poll-sync)
- Not needed initially — the missed-call/SMS use case is push-shaped. Revisit only if
  webhook event coverage is incomplete and a periodic call-history poll is needed as a
  backstop.

## Risk tier (rules.py)
- `(goto_connect, missed_call)` → `log_flag` — vault-only (log + maybe touch
  `last_contact_date`/flag on the matching `prospect`); no outbound action.
- `(goto_connect, sms_received)` → `supervised` — an inbound text from a prospect/family
  member could warrant a reply; any reply is external-facing and must be drafted as a
  `create_task` for Brennen to approve, not sent automatically.

## Secrets to add to .env
- `GOTO_CONNECT_CLIENT_ID` / `GOTO_CONNECT_CLIENT_SECRET` (OAuth2 — confirm grant type from
  docs) and/or `GOTO_CONNECT_WEBHOOK_SECRET` if webhooks are body-signed.

## Implementation steps (for the next agent)
1. Brennen to obtain developer/API access to GoTo Connect (developer.goto.com) and confirm:
   OAuth2 grant type + scopes needed for call/SMS events, webhook signature scheme, and the
   event catalog.
2. Copy `nexus/connectors/example/` → `nexus/connectors/goto_connect/`.
3. `webhook.py`: `NAME = "goto_connect"`, `SIGNATURE_HEADER`, `secret()` reading the new env
   var, `parse()` mapping GoTo's payload → `Stimulus(source="goto_connect", kind=...)`, and
   `_KIND_MAP` for `call.missed` / `message.received` (or GoTo's actual event names).
4. If subscriptions must be registered via API call (not a dashboard toggle), add
   `scripts/subscribe_goto_connect.py` to (re)register them.
5. Register in `nexus/connectors/ingress/routes.py` → `CONNECTORS`.
6. Add the two rows above to `nexus/connectors/ingress/rules.py`.
7. In `agents/toolset.py` / `tools/__init__.py`, consider a read tool to look up a caller's
   recent call/SMS history by phone number, so the agent can match it to a `prospect` and
   draft an informed reply — read-only, never a send method.
8. Add secrets to `.env` / `.env.example`.
9. Test: trigger a real or simulated missed call / inbound SMS in a GoTo Connect
   sandbox/test line and verify it lands in the event log and, when the number matches a
   known prospect/family contact, produces a `log_flag` or `supervised` task as expected.

## Open questions / unknowns
- Exact GoTo Connect API product and endpoints for call/SMS webhooks (confirm at
  developer.goto.com once Brennen has API access).
- Webhook signature scheme and header name.
- How to match an inbound phone number to an existing `prospect` (exact match on `phone` or
  a `family_contacts[].phone`, and what to do on no match — likely just log, no entity
  update).
- Whether GoTo Connect's plan tier includes the developer API / webhooks, or requires an
  upgrade.
