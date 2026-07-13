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

## Status: IMPLEMENTED (2026-07-13)

`nexus/connectors/goto_connect/` is built and tested against the live shapes below:
`stream.py` (persistent WebSocket consumer, started from the app lifespan; channel state
in `vault/system/goto_connect/state.json`), `events.py` (frame→Stimulus; missed call =
UchEvent without answerTime), `client.py` (read-only OAuth client + `goto_lookup_history`
/ `goto_get_voicemail` tools, wired into `agents/toolset.py` and the `tools()` MCP seam),
`sync.py` (missed-call gap-fill, `goto-connect-sync` in DETERMINISTIC_JOBS). Answered
calls are logged but never dispatched. Voicemail transcription returns NOT_FOUND on this
account — likely needs enabling in GoTo admin; the tool degrades gracefully.

## Original implementation steps (superseded — kept for context)
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

## Confirmed via live API recon (2026-07-13, scripts/goto_oauth.py)

OAuth client is created and authorized (principal `brennen@shsgreaternaperville.com`,
broad read + notifications-manage scopes granted). Tokens live at
`vault/system/goto_connect/oauth.json`; access token ~1h, refresh token long-lived.
Auth host: `https://authentication.logmeininc.com/oauth` (Basic auth on /token).
Raw payload dumps: `docs/connectors/goto-samples/` (gitignored — real customer data).

- **accountKey** `6327799820468129299` — from `GET /identity/v1/Users/me` →
  `urn:scim:schemas:extension:getgo:1.0` → `accounts[0].value`. Required by most APIs.
- **Webhooks are unsigned.** `POST /notification-channel/v1/channels/{nickname}` registers
  a webhook (or WebSocket) channel; deliveries are plain JSON POSTs — no HMAC/signature
  header. Verification must be a secret token embedded in the callback URL
  (`GOTO_CONNECT_WEBHOOK_SECRET`), constant-time compared at ingress.
- **Call Events API requires WebSocket channels** (`/call-events/v1/subscriptions` rejects
  webhook channels). For push-to-webhook, use **Call History notifications**
  (`call-history.v1.notifications.manage`) and/or poll `GET /call-history/v1/calls
  ?accountKey=&startTime=&endTime=` — confirmed working; items carry `legId`,
  `caller/callee {name, number}`, `direction`, `startTime`, `answerTime` (absent ⇒ never
  answered ⇒ missed), `duration` (ms), `hangupCause` (Q.850), `ownerPhoneNumber`.
- **SMS**: `GET /messaging/v1/conversations?ownerPhoneNumber=` — inbox access is
  per-number per-principal (Brennen's token sees `+16303602784`; other DIDs 403).
  Message schema: `id`, `timestamp`, `direction` (`IN`/`OUT`), `body`,
  `authorPhoneNumber`, `contactPhoneNumbers`, `media`, `labels`. Event subscriptions
  exist for `INBOUND_MESSAGE` etc. (`messaging.v1.notifications.manage`).
- **Voicemail has no list endpoint** (`GET /voicemail/v1/voicemails` → 405; every scoped
  variant 404). It is notification-driven: subscribe with
  `voicemail.v1.notifications.manage`, receive a `voicemailId`, then
  `GET /voicemail/v1/voicemails/{id}` (+ media/transcription) — item endpoint confirmed.
- Account DIDs (voice-admin): `+16303602005` (main, routes to ext), `+16303602780`,
  `+16303602784` (Brennen), `+16303602788`. Lines: `GET /users/v1/lines?accountKey=`.

## Live event shapes (confirmed 2026-07-13 via WebSocket recon, scripts/goto_ws_recon.py)

Every frame is `{event: "Notification", eventId, timestamp, data: {source, type, content}}`;
discriminate on `(data.source, data.type)`. Raw captures: `goto-samples/events/`.

- `("call-history", "UchEvent")` — fires ONCE at call end. `content` = the same row shape
  as the REST list: caller/callee `{name, number}`, `startTime`, `answerTime`, `duration`
  (ms), `hangupCause`, `ownerPhoneNumber`, `legId`, `accountKey`, `userKey`.
  **Missed call = no `answerTime` (duration 0)** — voicemail pickup does NOT set it.
  This is the primary trigger; call-events state tracking is unnecessary.
- `("messaging", "message")` — `content` is the message object (`id`, `direction: IN`,
  `authorPhoneNumber`, `ownerPhoneNumber`, `body`, `media`, `accountKey`). Subscriptions
  succeeded for ALL account DIDs including ones whose inbox REST reads 403 — push
  coverage is not gated by inbox sharing.
- `("VOICEMAIL", "NEW_VOICEMAIL")` — `content` has `voicemailId` (feed to
  `GET /voicemail/v1/voicemails/{id}` — confirmed 200 — and
  `.../transcription`), `voicemailboxId`, `extensionNumber`, `callerName`/`callerNumber`,
  `calledNumber`, `durationMs`, and `legId` that links back to the missed-call UchEvent.
- `("call-events", "call-state")` — STARTING/ACTIVE/ENDING with participant statuses
  (`RINGING`/`CONNECTED`/`IN_INTERACTIVE_VOICE_RESPONSE`) + recordings/transcripts
  metadata (`transcriptEnabled: true` on this account). Optional enrichment only.
- `("notification-websocket", "WEBSOCKET_REFRESH_REQUIRED")` — housekeeping ~every 10
  min; plain reconnect to the same channel URL preserves subscriptions.
- CAUTION: UchEvent `caller`/`callee`/`direction` are leg-relative and unintuitive
  (extension appears as `callee` with `direction: OUTBOUND` on an inbound external call).
  Identify the external party as "the side whose number isn't a short extension", don't
  trust `direction`.
- Subscription shapes: call-events `{channelId, accountKeys:[{id, events:[STARTING,
  ACTIVE,ENDING]}]}`; call-history `{channelId, accountKey}`; voicemail `{channelId,
  voicemailboxId, events:["NEW_VOICEMAIL"]}` (one per box —
  `GET /voicemail/v1/voicemailboxes?accountKey=` lists them); messaging v1 `{channelId,
  ownerPhoneNumber, eventTypes:["INCOMING_MESSAGE"]}` (one per DID).

## Open questions / unknowns
- Production delivery: call-events rejects webhook channels; call-history / messaging /
  voicemail webhook-channel compatibility is untested. Either verify those accept a
  webhook channel, or run one persistent WebSocket consumer task in-process that feeds
  the same `Stimulus` path (leaning WS: one mechanism for all four, no public URL).
- Voicemail transcription endpoint (`/transcription`) returned `status: NOT_FOUND`
  immediately after deposit — confirm whether it populates after processing lag or needs
  enabling in GoTo admin.
- Whether reading the main line's (+16303602005) message history via REST needs that
  inbox shared with Brennen's user (events already arrive regardless).
- How to match an inbound phone number to an existing `prospect` (exact match on `phone` or
  a `family_contacts[].phone`, and what to do on no match — likely just log, no entity
  update).
