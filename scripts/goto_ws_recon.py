"""GoTo Connect WebSocket event recon (docs/connectors/goto-connect.md).

Creates a WebSocket notification channel, subscribes it to call-events / call-history /
messaging / voicemail notifications (printing every subscription response — failures are
schema recon too), then listens and dumps each received event frame to
docs/connectors/goto-samples/events/ so the connector's parse() can be written against
real payloads.

Usage:
    python scripts/goto_ws_recon.py [listen_seconds]     # default 600

While it listens, trigger test traffic on the GoTo line: an answered call, an unanswered
call (let it ring out to voicemail), and an inbound SMS.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.goto_oauth import API_BASE, SAMPLES_DIR, refresh  # noqa: E402

ACCOUNT_KEY = "6327799820468129299"
EVENTS_DIR = SAMPLES_DIR / "events"


def _show(label: str, resp: httpx.Response) -> dict | None:
    body: object
    try:
        body = resp.json()
    except ValueError:
        body = resp.text[:500]
    print(f"{resp.status_code:>3}  {label}")
    print("     " + json.dumps(body)[:400])
    (SAMPLES_DIR / f"ws-{label.split()[0]}.json").write_text(
        json.dumps({"label": label, "status": resp.status_code, "body": body}, indent=2),
        encoding="utf-8",
    )
    return body if isinstance(body, dict) and resp.status_code < 300 else None


def setup(client: httpx.Client) -> tuple[str, str]:
    """Create the WS channel and all subscriptions; return (channelId, wss URL).

    Confirmed shapes (2026-07-13 recon): channelType is "WebSockets"; voicemail wants
    {voicemailboxId, events:["NEW_VOICEMAIL"]}; messaging v1 wants
    {ownerPhoneNumber, eventTypes:["INCOMING_MESSAGE"]} and 403s on inboxes the token's
    principal can't see; call-events accepts webhook-incompatible WS channels only.
    """
    nick = f"nexus-recon-{datetime.now(UTC).strftime('%H%M%S')}"  # fresh channel per run
    chan = _show(
        f"channel POST /notification-channel/v1/channels/{nick}",
        client.post(
            f"/notification-channel/v1/channels/{nick}",
            json={"channelType": "WebSockets"},
        ),
    )
    if chan is None:
        sys.exit("could not create a WebSocket notification channel — see dump above")

    channel_id = chan.get("channelId", "")
    ws_url = _find_wss(chan)
    if not channel_id or not ws_url:
        sys.exit(f"channel created but no channelId/wss URL found: {json.dumps(chan)[:400]}")
    print(f"\nchannelId = {channel_id}\nws url    = {ws_url}\n")

    _show(
        "call-events POST /call-events/v1/subscriptions",
        client.post(
            "/call-events/v1/subscriptions",
            json={
                "channelId": channel_id,
                "accountKeys": [
                    {"id": ACCOUNT_KEY, "events": ["STARTING", "ACTIVE", "ENDING"]}
                ],
            },
        ),
    )
    _show(
        "call-history POST /call-history/v1/subscriptions",
        client.post(
            "/call-history/v1/subscriptions",
            json={"channelId": channel_id, "accountKey": ACCOUNT_KEY},
        ),
    )

    boxes = client.get("/voicemail/v1/voicemailboxes", params={"accountKey": ACCOUNT_KEY})
    for box in boxes.json().get("items", []) if boxes.status_code == 200 else []:
        _show(
            f"voicemail-{box['extensionNumber']} POST /voicemail/v1/subscriptions",
            client.post(
                "/voicemail/v1/subscriptions",
                json={
                    "channelId": channel_id,
                    "voicemailboxId": box["voicemailboxId"],
                    "events": ["NEW_VOICEMAIL"],
                },
            ),
        )

    numbers = client.get("/voice-admin/v1/phone-numbers", params={"accountKey": ACCOUNT_KEY})
    for item in numbers.json().get("items", []) if numbers.status_code == 200 else []:
        _show(
            f"messaging-{item['number'].lstrip('+')} POST /messaging/v1/subscriptions",
            client.post(
                "/messaging/v1/subscriptions",
                json={
                    "channelId": channel_id,
                    "ownerPhoneNumber": item["number"],
                    "eventTypes": ["INCOMING_MESSAGE"],
                },
            ),
        )
    return channel_id, ws_url


def _find_wss(obj: object) -> str | None:
    """Depth-first hunt for a wss:// URL anywhere in the channel response."""
    if isinstance(obj, str) and obj.startswith("wss://"):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            if (hit := _find_wss(v)) is not None:
                return hit
    if isinstance(obj, list):
        for v in obj:
            if (hit := _find_wss(v)) is not None:
                return hit
    return None


async def listen(ws_url: str, seconds: int) -> None:
    """Dump every frame; reconnect on WEBSOCKET_REFRESH_REQUIRED or server close."""
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    deadline = asyncio.get_event_loop().time() + seconds
    print(f"listening for {seconds}s — trigger test calls/SMS/voicemail now …", flush=True)
    while (remaining := deadline - asyncio.get_event_loop().time()) > 0:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                while (remaining := deadline - asyncio.get_event_loop().time()) > 0:
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 30))
                    except TimeoutError:
                        continue
                    n += 1
                    stamp = datetime.now(UTC).strftime("%H%M%S")
                    out = EVENTS_DIR / f"event-{stamp}-{n:03d}.json"
                    try:
                        pretty = json.dumps(json.loads(frame), indent=2)
                    except (ValueError, TypeError):
                        pretty = repr(frame)
                    out.write_text(pretty, encoding="utf-8")
                    print(f"[{stamp}] event #{n} ({len(pretty)}B) -> {out.name}", flush=True)
                    print("   " + pretty.replace("\n", " ")[:300], flush=True)
                    if "WEBSOCKET_REFRESH_REQUIRED" in pretty:
                        print("   refresh requested — reconnecting", flush=True)
                        break  # drop to the outer loop and reconnect
        except websockets.ConnectionClosed as exc:
            print(f"connection closed ({exc}) — reconnecting", flush=True)
        except OSError as exc:
            print(f"connect failed ({exc}) — retrying in 5s", flush=True)
            await asyncio.sleep(5)
    print(f"\ndone — {n} event(s) captured in {EVENTS_DIR}", flush=True)


def main() -> None:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    token = refresh()
    client = httpx.Client(
        base_url=API_BASE,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30.0,
    )
    _, ws_url = setup(client)
    asyncio.run(listen(ws_url, seconds))


if __name__ == "__main__":
    main()
