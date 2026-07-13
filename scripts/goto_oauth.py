"""GoTo Connect OAuth2 bootstrap + API prober (docs/connectors/goto-connect.md, step 1).

Usage:
    python scripts/goto_oauth.py authorize   # one-time browser consent -> tokens on disk
    python scripts/goto_oauth.py refresh     # force a refresh-token exchange (sanity check)
    python scripts/goto_oauth.py probe       # hit read endpoints, dump raw payloads to
                                             # docs/connectors/goto-samples/ (schema recon)

Prereqs: an OAuth client created at https://developer.goto.com with redirect URI
    http://localhost:8765/callback
and GOTO_CONNECT_CLIENT_ID / GOTO_CONNECT_CLIENT_SECRET filled in .env.

Tokens are persisted at vault/system/goto_connect/oauth.json (inside the volume, outside
the note corpus via io.NON_NOTE_DIRS). Access tokens last ~1h; the refresh token is
long-lived, so `authorize` is one-time unless scopes change.

Auth host (confirmed 2026-07): https://authentication.logmeininc.com/oauth
"""

from __future__ import annotations

import base64
import json
import secrets
import sys
import threading
import webbrowser
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexus.config import settings  # noqa: E402

AUTH_BASE = "https://authentication.logmeininc.com/oauth"
API_BASE = "https://api.goto.com"
REDIRECT_URI = "http://localhost:8765/callback"
TOKEN_PATH = settings.vault_path / "system" / "goto_connect" / "oauth.json"
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "docs" / "connectors" / "goto-samples"


def _basic_auth() -> str:
    cid, csec = settings.goto_connect_client_id, settings.goto_connect_client_secret
    if not cid or not csec:
        sys.exit("Set GOTO_CONNECT_CLIENT_ID and GOTO_CONNECT_CLIENT_SECRET in .env first.")
    return base64.b64encode(f"{cid}:{csec}".encode()).decode()


def _save_tokens(tok: dict) -> None:
    tok["obtained_at"] = datetime.now(UTC).isoformat()
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(tok, indent=2), encoding="utf-8")
    print(f"tokens saved -> {TOKEN_PATH}")
    print(f"granted scopes: {tok.get('scope', '(none reported)')}")


def _load_tokens() -> dict:
    if not TOKEN_PATH.is_file():
        sys.exit("No tokens on disk — run `python scripts/goto_oauth.py authorize` first.")
    return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))


def _token_request(data: dict) -> dict:
    resp = httpx.post(
        f"{AUTH_BASE}/token",
        data=data,
        headers={
            "Authorization": f"Basic {_basic_auth()}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30.0,
    )
    if resp.status_code != 200:
        sys.exit(f"token endpoint {resp.status_code}: {resp.text}")
    return resp.json()


def authorize() -> None:
    _basic_auth()  # fail fast on missing creds before opening a browser
    state = secrets.token_urlsafe(16)
    url = f"{AUTH_BASE}/authorize?" + urlencode(
        {
            "client_id": settings.goto_connect_client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "state": state,
        }
    )

    code_box: dict[str, str] = {}
    got_code = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server API
            qs = parse_qs(urlparse(self.path).query)
            if qs.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"state mismatch")
                return
            code_box["code"] = qs.get("code", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorized. You can close this tab and return to the terminal.")
            got_code.set()

        def log_message(self, *args):  # silence request logging
            pass

    server = HTTPServer(("localhost", 8765), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    print("Opening browser for GoTo consent (or visit manually):\n  " + url)
    webbrowser.open(url)
    if not got_code.wait(timeout=300):
        sys.exit("Timed out after 5 minutes waiting for the OAuth redirect.")

    tok = _token_request(
        {
            "grant_type": "authorization_code",
            "code": code_box["code"],
            "redirect_uri": REDIRECT_URI,
        }
    )
    _save_tokens(tok)
    print(f"principal: {tok.get('principal', '?')}")


def refresh() -> str:
    tok = _load_tokens()
    obtained = datetime.fromisoformat(tok.get("obtained_at", "1970-01-01T00:00:00+00:00"))
    if datetime.now(UTC) - obtained < timedelta(seconds=int(tok.get("expires_in", 0)) - 60):
        return tok["access_token"]  # still fresh
    new = _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    new.setdefault("refresh_token", tok["refresh_token"])  # some IdPs omit it on refresh
    _save_tokens(new)
    return new["access_token"]


# --- probe: dump real payloads so the connector's parse()/client can be written to fact ----

def _dump(name: str, path: str, resp: httpx.Response) -> object:
    try:
        body: object = resp.json()
    except ValueError:
        body = resp.text[:2000]
    out = SAMPLES_DIR / f"{name}.json"
    out.write_text(
        json.dumps({"path": path, "status": resp.status_code, "body": body}, indent=2),
        encoding="utf-8",
    )
    print(f"{resp.status_code:>3}  {path}  -> {out.relative_to(SAMPLES_DIR.parent.parent)}")
    return body if resp.status_code == 200 else None


def probe() -> None:
    token = refresh()
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(
        base_url=API_BASE, headers={"Authorization": f"Bearer {token}"}, timeout=30.0
    )
    get = lambda name, path, **params: _dump(name, path, client.get(path, params=params))  # noqa: E731

    # Who am I / account key (SCIM identity — accountKey unlocks the per-account APIs).
    me = get("identity-me", "/identity/v1/Users/me")
    account_key = ""
    if isinstance(me, dict):
        accounts = me.get("urn:scim:schemas:extension:getgo:1.0", {}).get("accounts", [])
        account_key = str(accounts[0]["value"]) if accounts else ""
        print(f"  accountKey = {account_key or '(not found)'}")

    # The account's DIDs — messaging + call-history queries key off a phone number.
    numbers = get(
        "phone-numbers", "/voice-admin/v1/phone-numbers", accountKey=account_key, pageSize=10
    )

    # Call history (has dispositions like missed) + call reports (needs a time window).
    week_ago = (datetime.now(UTC) - timedelta(days=7)).isoformat(timespec="seconds")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    get(
        "call-history",
        "/call-history/v1/calls",
        accountKey=account_key,
        pageSize=5,
        startTime=week_ago,
        endTime=now,
    )
    get(
        "call-reports",
        "/call-reports/v1/reports/user-activity",
        startTime=week_ago,
        endTime=now,
        pageSize=5,
    )

    # Voicemail — the bare collection was 405; try user- and org-scoped variants.
    user_key = str(me.get("id", "")) if isinstance(me, dict) else ""
    if user_key:
        get("voicemails-user", f"/voicemail/v1/users/{user_key}/voicemails", pageSize=5)
        get("voicemails-user-mailbox", f"/voicemail/v1/mailboxes/{user_key}/messages", pageSize=5)

    # Messaging conversations — inbox access is per-number, so try every DID until one
    # answers (403 = this principal can't see that inbox, not a dead end).
    dids = [i["number"] for i in numbers.get("items", [])] if isinstance(numbers, dict) else []
    for did in dids:
        body = get(
            f"messaging-conversations-{did.lstrip('+')}",
            "/messaging/v1/conversations",
            ownerPhoneNumber=did,
        )
        if body is not None:
            break
    if not dids:
        print("  (skipped messaging — no phone number found yet)")

    print("\nNon-200s usually mean a missing scope on the OAuth client or a different API")
    print("path for this account — paste the outputs back into the chat and we'll adjust.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "authorize":
        authorize()
    elif cmd == "refresh":
        print("access token ok:", refresh()[:24] + "…")
    elif cmd == "probe":
        probe()
    else:
        print(__doc__)
