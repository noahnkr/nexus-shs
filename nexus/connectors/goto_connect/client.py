"""Outbound READ client + OAuth2 token store for GoTo Connect.

Auth: OAuth2 authorization-code with a long-lived refresh token, bootstrapped ONCE by
`scripts/goto_oauth.py authorize`. Tokens persist at
`vault/system/goto_connect/oauth.json` (inside the one volume, outside the note corpus via
io.NON_NOTE_DIRS). Access tokens last ~1h; this module refreshes on expiry and once more
on a 401.

READ-ONLY by design: GoTo Connect can send SMS, place calls, and forward
voicemails — those endpoints are deliberately NOT wrapped here. External-facing actions
become `create_task` drafts; the trust boundary is the absence of the capability.

Confirmed API surface (live recon 2026-07-13, docs/connectors/goto-connect.md):
  GET  /call-history/v1/calls?accountKey&startTime&endTime      (UchEvent row shape)
  GET  /messaging/v1/conversations?ownerPhoneNumber=            (403 => inbox not shared)
  GET  /voicemail/v1/voicemails/{id} (+ /transcription)
  GET  /voicemail/v1/voicemailboxes?accountKey=
  GET  /voice-admin/v1/phone-numbers?accountKey=
  POST /notification-channel/v1/channels/{nick}  {"channelType": "WebSockets"}
  POST /call-history/v1/subscriptions · /voicemail/v1/subscriptions ·
       /messaging/v1/subscriptions                               (see stream.py)
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from nexus.config import settings

log = logging.getLogger(__name__)

AUTH_BASE = "https://authentication.logmeininc.com/oauth"
API_BASE = "https://api.goto.com"
_REFRESH_MARGIN = 60  # seconds before nominal expiry to refresh proactively


class GoToAuthError(RuntimeError):
    """Credentials/token problems the caller can surface but not fix at runtime."""


class TokenStore:
    """oauth.json custody: load, refresh-when-stale, save. One file, one source of truth."""

    def __init__(self, path: Path | None = None, transport: httpx.BaseTransport | None = None):
        self.path = path or settings.vault_path / "system" / "goto_connect" / "oauth.json"
        self._transport = transport

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("goto_connect: corrupt oauth.json at %s", self.path)
            return None

    def save(self, tok: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(tok, indent=2), encoding="utf-8")

    def access_token(self, *, force_refresh: bool = False) -> str:
        tok = self.load()
        if tok is None:
            raise GoToAuthError(
                "no GoTo tokens on disk — run `python scripts/goto_oauth.py authorize`"
            )
        if not force_refresh and not self._stale(tok):
            return tok["access_token"]
        return self._refresh(tok)

    @staticmethod
    def _stale(tok: dict[str, Any]) -> bool:
        obtained = tok.get("obtained_at")
        if not obtained:
            return True
        age = datetime.now(UTC) - datetime.fromisoformat(obtained)
        return age >= timedelta(seconds=int(tok.get("expires_in", 0)) - _REFRESH_MARGIN)

    def _refresh(self, tok: dict[str, Any]) -> str:
        cid, csec = settings.goto_connect_client_id, settings.goto_connect_client_secret
        if not cid or not csec:
            raise GoToAuthError("GOTO_CONNECT_CLIENT_ID / _CLIENT_SECRET not configured")
        basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
        with httpx.Client(timeout=30.0, transport=self._transport) as client:
            resp = client.post(
                f"{AUTH_BASE}/token",
                data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
                headers={"Authorization": f"Basic {basic}"},
            )
        if resp.status_code != 200:
            raise GoToAuthError(f"token refresh failed ({resp.status_code}): {resp.text[:200]}")
        new = resp.json()
        new.setdefault("refresh_token", tok["refresh_token"])  # some IdPs omit it on refresh
        new["obtained_at"] = datetime.now(UTC).isoformat()
        # carry forward cached non-token facts (account_key)
        for key in ("account_key",):
            if key in tok:
                new.setdefault(key, tok[key])
        self.save(new)
        return new["access_token"]


class GoToClient:
    def __init__(
        self,
        store: TokenStore | None = None,
        base_url: str = API_BASE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._store = store or TokenStore()
        self._http = httpx.Client(base_url=base_url, timeout=30.0, transport=transport)

    # --- plumbing ----------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = self._store.access_token()
        resp = self._http.request(
            method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
        )
        if resp.status_code == 401:  # stale/revoked mid-flight: refresh once and retry
            token = self._store.access_token(force_refresh=True)
            resp = self._http.request(
                method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
        return resp

    def _get_json(self, path: str, params: dict | None = None) -> dict[str, Any]:
        resp = self._request("GET", path, params=params)
        resp.raise_for_status()
        return resp.json()

    def account_key(self) -> str:
        """The GoTo account key, cached in oauth.json after the first identity lookup."""
        tok = self._store.load() or {}
        if tok.get("account_key"):
            return tok["account_key"]
        me = self._get_json("/identity/v1/Users/me")
        accounts = me.get("urn:scim:schemas:extension:getgo:1.0", {}).get("accounts", [])
        if not accounts:
            raise GoToAuthError("GoTo identity has no accounts — cannot derive accountKey")
        tok["account_key"] = str(accounts[0]["value"])
        self._store.save(tok)
        return tok["account_key"]

    # --- reads -------------------------------------------------------------------------

    def recent_calls(
        self, since: datetime, until: datetime | None = None, page_size: int = 200
    ) -> list[dict[str, Any]]:
        """Completed-call rows (same shape as the UchEvent push frame), oldest first not
        guaranteed — callers should not assume ordering."""
        params: dict[str, str] = {
            "accountKey": self.account_key(),
            "startTime": since.isoformat(timespec="seconds"),
            "endTime": (until or datetime.now(UTC)).isoformat(timespec="seconds"),
            "pageSize": str(page_size),
        }
        body = self._get_json("/call-history/v1/calls", params=params)
        items: list[dict[str, Any]] = list(body.get("items", []))
        while marker := body.get("nextPageMarker"):
            body = self._get_json(
                "/call-history/v1/calls", params={**params, "pageMarker": marker}
            )
            items.extend(body.get("items", []))
        return items

    def conversations(self, owner_phone: str) -> list[dict[str, Any]]:
        """SMS conversations for one of OUR numbers. 403 (inbox not shared with the token's
        principal) degrades to [] — push events still arrive for such inboxes."""
        resp = self._request(
            "GET", "/messaging/v1/conversations", params={"ownerPhoneNumber": owner_phone}
        )
        if resp.status_code == 403:
            return []
        resp.raise_for_status()
        return resp.json().get("items", [])

    def list_phone_numbers(self) -> list[dict[str, Any]]:
        return self._get_json(
            "/voice-admin/v1/phone-numbers", params={"accountKey": self.account_key()}
        ).get("items", [])

    def list_voicemailboxes(self) -> list[dict[str, Any]]:
        return self._get_json(
            "/voicemail/v1/voicemailboxes", params={"accountKey": self.account_key()}
        ).get("items", [])

    def get_voicemail(self, voicemail_id: str) -> dict[str, Any]:
        return self._get_json(f"/voicemail/v1/voicemails/{voicemail_id}")

    def get_voicemail_transcription(self, voicemail_id: str) -> dict[str, Any]:
        """{"status": "NOT_FOUND"} until (unless) GoTo finishes/enables transcription."""
        return self._get_json(f"/voicemail/v1/voicemails/{voicemail_id}/transcription")

    # --- notification channel + subscriptions (used by stream.py) -----------------------

    def create_channel(self, nickname: str) -> dict[str, Any]:
        resp = self._request(
            "POST",
            f"/notification-channel/v1/channels/{nickname}",
            json={"channelType": "WebSockets"},
        )
        resp.raise_for_status()
        return resp.json()

    def subscribe_all(self, channel_id: str) -> None:
        """Subscribe the channel to every push source we consume. Idempotent enough to
        re-run on channel recreation; per-source failures are logged, not fatal."""
        self._subscribe(
            "call-history", "/call-history/v1/subscriptions",
            {"channelId": channel_id, "accountKey": self.account_key()},
        )
        for box in self.list_voicemailboxes():
            self._subscribe(
                f"voicemail:{box.get('extensionNumber')}", "/voicemail/v1/subscriptions",
                {
                    "channelId": channel_id,
                    "voicemailboxId": box["voicemailboxId"],
                    "events": ["NEW_VOICEMAIL"],
                },
            )
        for item in self.list_phone_numbers():
            self._subscribe(
                f"messaging:{item.get('number')}", "/messaging/v1/subscriptions",
                {
                    "channelId": channel_id,
                    "ownerPhoneNumber": item["number"],
                    "eventTypes": ["INCOMING_MESSAGE"],
                },
            )

    def _subscribe(self, label: str, path: str, body: dict[str, Any]) -> None:
        try:
            resp = self._request("POST", path, json=body)
            if resp.status_code >= 300:
                log.warning("goto_connect: subscribe %s -> %s %s",
                            label, resp.status_code, resp.text[:200])
        except httpx.HTTPError:
            log.exception("goto_connect: subscribe %s failed", label)


# --- loop/MCP tool functions (plain, degrade gracefully without credentials) ------------


def goto_lookup_history(phone: str, days: int = 14) -> dict[str, Any]:
    """Recent call + SMS history with `phone`, matched across the account.

    Read-only; backs both the loop toolset and the MCP tool. SMS coverage is limited to
    inboxes the token's principal can read (others 403 -> skipped).
    """
    from nexus.connectors.goto_connect.events import normalize_phone

    target = normalize_phone(phone)
    if target is None:
        return {"error": f"not a routable phone number: {phone!r}"}
    try:
        client = GoToClient()
        since = datetime.now(UTC) - timedelta(days=days)
        calls = []
        for row in client.recent_calls(since=since):
            numbers = {
                normalize_phone((row.get("caller") or {}).get("number")),
                normalize_phone((row.get("callee") or {}).get("number")),
            }
            if target in numbers:
                calls.append(
                    {
                        "start_time": row.get("startTime"),
                        "answered": bool(row.get("answerTime")),
                        "duration_ms": row.get("duration"),
                        "our_number": row.get("ownerPhoneNumber"),
                    }
                )
        threads = []
        for item in client.list_phone_numbers():
            for convo in client.conversations(item["number"]):
                contacts = {normalize_phone(n) for n in convo.get("contactPhoneNumbers", [])}
                if target in contacts:
                    last = convo.get("lastMessage") or {}
                    threads.append(
                        {
                            "our_number": convo.get("ownerPhoneNumber"),
                            "last_message_at": convo.get("lastMessageTimestamp"),
                            "last_direction": last.get("direction"),
                            "last_body": last.get("body"),
                            "unread": convo.get("unreadMessagesCount"),
                        }
                    )
        return {"phone": target, "calls": calls, "sms_threads": threads}
    except (GoToAuthError, httpx.HTTPError) as exc:
        return {"error": str(exc)}


def goto_get_voicemail(voicemail_id: str) -> dict[str, Any]:
    """Voicemail metadata + transcription text (when GoTo has produced one)."""
    try:
        client = GoToClient()
        meta = client.get_voicemail(voicemail_id)
        out = {
            "caller_name": meta.get("callerName"),
            "caller_number": meta.get("callerNumber"),
            "called_number": meta.get("calledNumber"),
            "duration_ms": meta.get("durationMs"),
            "received_at": meta.get("timestamp"),
            "status": meta.get("status"),
        }
        transcript = client.get_voicemail_transcription(voicemail_id)
        if transcript.get("status") != "NOT_FOUND":
            out["transcription"] = transcript
        return out
    except (GoToAuthError, httpx.HTTPError) as exc:
        return {"error": str(exc)}
