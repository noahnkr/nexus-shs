"""Connector `welcomehome` — WelcomeHome CRM (docs/connectors/welcomehome.md).

Pull-only: WelcomeHome has no webhooks, so this connector is a deterministic poll-sync
 over the live, paginated bulk CSV export API. It reconciles Prospect rows into
`prospect` entity notes and manufactures new_prospect / stage_changed / prospect_stale
deltas back through the ordinary classify -> dispatch path.

  - client.py  outbound : typed READ client for the Exports API (+ stages/lead_sources)
  - sync.py    pull     : run_sync(), registered in jobs.DETERMINISTIC_JOBS

No webhook.py — there is no push path for this source. No write/send methods anywhere:
WelcomeHome is read-only from Nexus's side.
"""

NAME = "welcomehome"
