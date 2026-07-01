# Onboarding profile — raw record

*Running notes captured during `/onboard`. Source of truth for decisions; later agents can
re-read this to understand why the config looks the way it does.*

## Phase 1 — You & your voice
- Owner: Brennen Roberts, owner of the franchise Seniors Helping Seniors — Greater Naperville.
- Email: brennen@shsgreaternaperville.com
- SMS: 630-484-3579
- Timezone: America/Chicago
- Notify via: email + SMS
- Tone: formal/professional
- Autonomy: draft-and-wait to start (external-facing always queued for approval; internal
  vault updates happen automatically without asking). Promote specific (source,kind) to
  autonomous later once proven.
- Standing hard rules: none yet — revisit as they come up.

## Phase 2 — The business
- Business: Seniors Helping Seniors — Greater Naperville, a home care franchise serving
  seniors (non-medical + personal + specialized care).
- Offerings: companion care (companionship, light housekeeping, meal prep, errands),
  personal care (bathing, dressing, mobility, hygiene), specialized care (dementia/
  Alzheimer's, post-hospital/respite, transportation to appointments).
- Lead flow: leads come from aggregators (A Place for Mom, Care.com, etc.) via structured
  emails. WelcomeHome CRM auto-intakes these emails and loads the lead ("Prospect" in
  WelcomeHome) with basic info.
- Lead/Prospect stages (WelcomeHome): Inquiry → Attempted → Ct Made (Contact Made) →
  Visit Schld (Visit Scheduled) → Visit Cmplt (Visit Completed) → SOC (Start of Care).
- System of record for leads/clients: WelcomeHome CRM.
- Secondary systems: WellSky (caregiver scheduling + medical-related client info — likely
  PHI, needs a data boundary), GoTo Connect (VoIP + SMS phone system).
- Initial focus for the agent: the sales pipeline (lead intake through SOC), not post-SOC
  caregiver ops/billing (deferred).

## Phase 3 — What you track (entities)
- Single entity kind: `prospect` (a WelcomeHome lead through Start of Care). No separate
  `client` kind yet — post-SOC ops are deferred.
- Identity/dedup: no stable WelcomeHome ID wired up yet — dedup on name + phone/email for
  now; revisit `source_ref` once the WelcomeHome connector lands.
- Lifecycle (`Status`): inquiry → attempted → contact_made → visit_scheduled →
  visit_completed → soc (mirrors WelcomeHome's Inquiry/Attempted/Ct Made/Visit Schld/
  Visit Cmplt/SOC).
- Fields: referral_source (aggregator), service_lines (list: Unknown / Companion Care /
  Personal Care / Respite Care / Specialized Care / VA Prospect), phone, email,
  family_contacts (list of {name, phone, email} — a prospect can have several family
  decision-makers), inquiry_date, last_contact_date, next_follow_up.
- Data boundary: no medical/PHI detail in prospect notes — that stays in WellSky; only a
  brief non-sensitive care-need summary may appear in the note body.

## Phase 4 — What you know (reference)
- Categories (`ReferenceCategory`): intake_script (how to talk to a new inquiry at each
  lead stage), pricing (rate sheets, hourly rates by service line, minimums, packages),
  service_sop (what each service line includes), policy_voice (general policy, brand
  voice/tone, FAQs).
- Audience (`Audience`): internal (default) vs client_facing. Reference material is mostly
  internal-only for now — staff use, not shared verbatim with prospects/families.

## Phase 5 — Your systems (connectors)
- WelcomeHome (top priority, build first): no webhooks for stage changes; has a paginated
  bulk CSV export API (https://crm.welcomehomesoftware.com/api-docs/index.html#tag/Exports)
  → planned as a poll-sync. Plan: `docs/connectors/welcomehome.md`.
- GoTo Connect: Brennen can get developer/API access; wants missed-call/SMS visibility on
  prospects/families → planned as a push (webhook) connector, pending confirmation of
  GoTo's webhook signature scheme. Plan: `docs/connectors/goto-connect.md`.
- WellSky: deferred — post-SOC caregiver scheduling + PHI, out of scope until post-SOC ops
  are in scope. No plan doc written yet.

## Phase 6 — Risk & autonomy
- (welcomehome, new_prospect) -> log_flag
- (welcomehome, stage_changed) -> log_flag
- (welcomehome, prospect_stale) -> log_flag
- (goto_connect, missed_call) -> log_flag
- (goto_connect, sms_received) -> supervised (any reply is external-facing -> draft)
- Confirmed with Brennen: vault-only updates auto-log + flag; nothing external-facing goes
  out without his approval.
- Notification transport: `agents/notify.py` logs by default — swap in email/SMS to
  brennen@shsgreaternaperville.com / 630-484-3579 when a transport is implemented.

## Phase 7 — Wrap up
- README retitled for Seniors Helping Seniors — Greater Naperville; pointed reconfiguration
  back at /onboard.
- .env.example updated: WELCOMEHOME_API_KEY, GOTO_CONNECT_CLIENT_ID/SECRET,
  GOTO_CONNECT_WEBHOOK_SECRET, OWNER_CONTACT filled with Brennen's email + SMS.
- pytest (18 passed, 1 skipped — agent-loop test needs ANTHROPIC_API_KEY) and ruff both
  green after the schema change (had to update tests/test_acceptance.py's placeholder
  "thing" kind -> "prospect" and the "hr" reference category -> "policy_voice").
- docs/SEEDING.md written.
