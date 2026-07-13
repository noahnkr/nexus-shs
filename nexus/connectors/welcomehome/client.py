"""Outbound READ client for WelcomeHome CRM.

API: https://crm.welcomehomesoftware.com/api-docs/index.html (OpenAPI at
/api-docs/v1/swagger.yaml). Confirmed from the spec:

  - Auth: `Authorization: Token token={api_key}`; `GET /api/ping` validates a token.
  - Bulk export: `GET /api/exports/community/{community_id}/table/{table}` returns live,
    paginated CSV (default 1000 rows/page, max 10000). `filters[updated_at_after]` is the
    documented re-poll strategy; the next page is a cursor URL in the `Link` header
    (`rel="next"`). A cursor may only be used 3 times per minute.
  - Stages are ACCOUNT-CONFIGURABLE (`GET /api/stages` -> id/name/position/system_type),
    so the sync translates `stage_id` through this map rather than assuming fixed labels.
  - All timestamps are UTC.

READ-ONLY by design: WelcomeHome is never written to from Nexus, so there are no
write/send methods here at all — nothing for the trust boundary to even exclude.
"""

from __future__ import annotations

import csv
from datetime import datetime
from io import StringIO
from typing import Any

import httpx

BASE_URL = "https://crm.welcomehomesoftware.com/api"


class WelcomeHomeClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Token token={api_key}"},
            timeout=30.0,
            follow_redirects=True,
            transport=transport,
        )

    def ping(self) -> dict[str, Any]:
        """Validate the token; returns e.g. {"account_id": 2, "lead_source_id": null}."""
        resp = self._http.get("/ping")
        resp.raise_for_status()
        return resp.json()

    def list_stages(self) -> list[dict[str, Any]]:
        """The account's pipeline stages (id/name/position/system_type), in order."""
        resp = self._http.get("/stages")
        resp.raise_for_status()
        return resp.json()

    def list_lead_sources(self) -> list[dict[str, Any]]:
        """The account's lead sources (id/name/category) — aggregators like A Place for Mom."""
        resp = self._http.get("/lead_sources")
        resp.raise_for_status()
        return resp.json()

    def export_prospects(self, since: datetime | None = None) -> list[dict[str, str]]:
        """Pull Prospect rows changed since `since` (all rows when None), as CSV dicts.

        Pages through the export until the `Link: <...>; rel="next"` header is absent.
        Column headers are table-prefixed (`prospects.id`, `stages.name`, ...). The rows
        do NOT include an `updated_at` column (the server-side filter still works), nor
        any resident name/contact columns — join `export_residents()` for those.
        """
        return self._export_table("Prospects", since)

    def export_residents(self) -> list[dict[str, str]]:
        """All Resident rows (`residents.prospect_id` + `people.*` name/contact columns)."""
        return self._export_table("Residents", None)

    def export_activities(self, since: datetime | None = None) -> list[dict[str, str]]:
        """Activity rows (`activities.record_type/record_id/completed_at`) since `since`."""
        return self._export_table("Activities", since)

    def _export_table(self, table: str, since: datetime | None) -> list[dict[str, str]]:
        url: str | None = f"/exports/community/all/table/{table}"
        params: dict[str, str] | None = {}
        if since is not None:
            params = {"filters[updated_at_after]": since.isoformat()}
        rows: list[dict[str, str]] = []
        while url:
            resp = self._http.get(url, params=params)
            resp.raise_for_status()
            rows.extend(csv.DictReader(StringIO(resp.text)))
            url = resp.links.get("next", {}).get("url")  # absolute cursor URL, or None
            params = None  # the cursor URL already carries the query
        return rows
