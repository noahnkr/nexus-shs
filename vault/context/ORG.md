# ORG — what the business is

*Always-on. Injected verbatim into every agent turn. Keep it short and stable — this is
context the agent should never have to look up.*

- **Business:** Seniors Helping Seniors — Greater Naperville, a franchise providing
  non-medical home care for seniors.
- **Model:** inbound leads (from senior-care aggregators) → phone/visit assessment → care
  plan → caregiver matched → ongoing in-home care service, billed for hours of care.
- **Customers:** seniors and their adult family members — roughly an even mix inquire and
  decide together.
- **Offerings:**
  - Companion care — companionship, light housekeeping, meal prep, errands.
  - Personal care — bathing, dressing, mobility assistance, hygiene support.
  - Specialized care — dementia/Alzheimer's care, post-hospital/respite care,
    transportation to appointments.
- **Operating rhythm:** America/Chicago. Leads are time-sensitive — aggregator leads often
  go to whoever responds first, so new inquiries should be responded to within the hour.

## Systems of record
*Where the truth lives today — the agent synthesizes across these (via connectors).*

- **WelcomeHome CRM** — leads ("Prospects") and clients; the primary system for the sales
  pipeline. Auto-intakes structured lead emails from aggregators.
- **WellSky** — caregiver scheduling and medical-related client information. Treated as the
  source of truth for anything PHI-adjacent (see data boundary below).
- **GoTo Connect** — VoIP phone system and SMS.

## Data boundary
- WellSky holds medical/PHI-level detail (diagnoses, medications, detailed records) for
  clients — this must **never** enter the Nexus vault.
- A brief, non-sensitive care-need summary (e.g. "has mobility needs", "early-stage memory
  care") is acceptable in vault entity notes; specifics stay in WellSky.

## Glossary
*Domain terms the agent must use correctly. Keep to the few that actually matter.*

- **Prospect** — a lead in WelcomeHome, before Start of Care.
- **Aggregator** — a senior-care referral source (A Place for Mom, Care.com, etc.) whose
  structured lead emails feed WelcomeHome.
- **Lead stage** — a Prospect's position in the pipeline: Inquiry → Attempted → Ct Made
  (Contact Made) → Visit Schld (Visit Scheduled) → Visit Cmplt (Visit Completed) → SOC.
- **SOC** — Start of Care; the point a Prospect becomes an active client.
