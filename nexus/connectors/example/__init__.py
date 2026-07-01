"""Sample connector `example` (spec §4) — the FORK SEAM template (§7 step 3).

Copy this package to `connectors/<your-source>/`, implement the three pieces, and register
it: add the webhook module to ingress.routes.CONNECTORS, and (if it polls) its sync to
jobs.DETERMINISTIC_JOBS.

A connector speaks one external system's dialect in either or both directions:
  - webhook.py  inbound  : secret() + parse() (+ optional signed_timestamp())
  - client.py   outbound : typed READ client (the loop wraps reads only)
  - sync.py     pull      : deterministic poll-sync for sources without good webhooks
"""

NAME = "example"
