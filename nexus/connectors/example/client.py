"""Outbound client for `example` — and the trust split.

An ordinary typed REST/OAuth client. The agent's toolset wraps its READ methods so the
loop can pull live context. Critically:

  Server-side agents get a source's READ tools but NEVER its SEND/WRITE tools. Mutating an
  external system is external-facing, so it goes through the approval queue like everything
  else. The send capability exists only on the OWNER'S post-approval path.

So: put read methods here for the loop to wrap; keep any write/send methods clearly
separated and OFF the loop's toolset (they belong to the owner's post-approval path).
"""

from __future__ import annotations

from typing import Any

import httpx


class ExampleClient:
    def __init__(self, base_url: str = "https://api.example.com", token: str | None = None) -> None:
        self._http = httpx.Client(base_url=base_url, headers=_auth(token))

    # --- READ methods (safe to wrap as agent tools) ---
    def search_records(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        raise NotImplementedError("example stub — GET /records?q=... ; read-only, wrappable.")

    def get_record(self, record_id: str) -> dict[str, Any]:
        raise NotImplementedError("example stub — GET /records/{id} ; read-only, wrappable.")

    # --- WRITE/SEND methods (OWNER post-approval path ONLY — never on the loop) ---
    def send_message(self, recipient: str, body: str) -> dict[str, Any]:
        raise NotImplementedError(
            "example stub — external-facing mutation. NOT exposed to the server-side loop; only "
            "reachable on the owner's post-approval path after a create_task is approved."
        )


def _auth(token: str | None) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"} if token else {}
